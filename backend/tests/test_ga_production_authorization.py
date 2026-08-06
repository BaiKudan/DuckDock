from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.verify_ga_production_authorization import (
    ALERTING_POLICY_SCHEMA_VERSION,
    ALERTING_SCHEMA_VERSION,
    ALERT_DELIVERY_SCHEMA_VERSION,
    ALERT_DELIVERY_SIGNATURE_NAMESPACE,
    APPROVAL_POLICY_SCHEMA_VERSION,
    ONCALL_ACK_SCHEMA_VERSION,
    ONCALL_ACK_SIGNATURE_NAMESPACE,
    REQUIRED_APPROVAL_ROLES,
    SCHEMA_VERSION,
    _approval_statement,
    _release_digest,
    _verify_ssh_signature,
    evaluate,
    lint_authorization,
)


def _evidence(tmp_path: Path, name: str, observed_at: datetime) -> dict[str, str]:
    path = tmp_path / f"{name}.json"
    path.write_text(f'{{"control":"{name}","passed":true}}\n', encoding="utf-8")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "observed_at": observed_at.isoformat(),
    }


def _document(tmp_path: Path, now: datetime) -> dict:
    observed_at = now - timedelta(minutes=5)
    commit = "a" * 40
    backend_image = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
    frontend_image = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
    controls = {
        "application_readiness": {
            "status": "READY",
            "pass_count": 14,
            "block_count": 0,
            "contract_version": "2.0.0",
            "contract_digest": "d" * 64,
            "database_revision": "20260804_0062",
            "expected_database_revision": "20260804_0062",
            "evidence": _evidence(tmp_path, "application", observed_at),
        },
        "tls": {
            "status": "PASS",
            "negotiated_protocols": ["TLSv1.2", "TLSv1.3"],
            "legacy_protocols_rejected": ["TLSv1", "TLSv1.1"],
            "certificate_days_remaining": 60,
            "hostname_verified": True,
            "hsts_max_age_seconds": 31_536_000,
            "evidence": _evidence(tmp_path, "tls", observed_at),
        },
        "secrets": {
            "status": "PASS",
            "provider": "External Secrets",
            "plaintext_env_persisted": False,
            "rotation_tested": True,
            "evidence": _evidence(tmp_path, "secrets", observed_at),
        },
        "network": {
            "status": "PASS",
            "public_tcp_ports": [443],
            "database_public": False,
            "redis_public": False,
            "object_store_direct_public": False,
            "default_deny_ingress": True,
            "egress_allowlist_enforced": True,
            "evidence": _evidence(tmp_path, "network", observed_at),
        },
        "alerting": {
            "status": "PASS",
            "test_notification_delivered": True,
            "resolved_notification_delivered": True,
            "oncall_schedule": "platform-primary",
            "evidence": _evidence(tmp_path, "alerting", observed_at),
        },
        "recovery": {
            "status": "PASSED",
            "offsite_media": True,
            "encrypted": True,
            "immutable_or_object_locked": True,
            "rpo_seconds": 0,
            "rto_seconds": 600,
            "mysql_rows_verified": 10,
            "objects_verified": 3,
            "git_repositories_verified": True,
            "evidence": _evidence(tmp_path, "recovery", observed_at),
        },
        "capacity": {
            "status": "PASSED",
            "sustained_seconds": 900,
            "sustained_rps": 50,
            "materialized_runs": 50_000,
            "error_rate": 0,
            "write_p95_ms": 50,
            "timeline_p95_ms": 30,
            "post_growth_query_passed": True,
            "evidence": _evidence(tmp_path, "capacity", observed_at),
        },
        "high_availability": {
            "status": "PASS",
            "replica_counts": {"backend": 3, "frontend": 3, "worker": 3, "beat": 1},
            "fault_domains_exercised": 2,
            "managed_mysql_ha": True,
            "managed_redis_ha": True,
            "object_store_ha": True,
            "rwx_repository_storage_ha": True,
            "node_failover_passed": True,
            "zone_failover_passed": True,
            "beat_recovery_passed": True,
            "evidence": _evidence(tmp_path, "ha", observed_at),
        },
        "security_assessment": {
            "status": "PASS",
            "independent": True,
            "provider": "Independent Security Lab",
            "open_critical": 0,
            "open_high": 0,
            "source_commit": commit,
            "backend_image": backend_image,
            "frontend_image": frontend_image,
            "evidence": _evidence(tmp_path, "security", observed_at),
        },
    }
    application_path = Path(controls["application_readiness"]["evidence"]["path"])
    application_path.write_text(
        json.dumps(
            {
                "schema_version": "duckdock-ga-target-readiness-v1",
                "scope": "target-production",
                "status": "READY",
                "passed": True,
                "observed_at": observed_at.isoformat(),
                "checked_at": (observed_at - timedelta(seconds=10)).isoformat(),
                "target_environment": "customer-production",
                "source_commit": commit,
                "images": {
                    "backend": {"name": backend_image},
                    "frontend": {"name": frontend_image},
                },
                "base_url": "https://duckdock.example.com",
                "transport": "network HTTPS against target",
                "profile_version": "duckdock-2-ga-readiness-v1",
                "pass_count": 14,
                "warn_count": 0,
                "block_count": 0,
                "contract_version": "2.0.0",
                "contract_digest": "d" * 64,
                "current_db_revision": "20260804_0062",
                "expected_db_revision": "20260804_0062",
                "checks": [{"key": f"check-{index}", "status": "PASS"} for index in range(14)],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    controls["application_readiness"]["evidence"]["sha256"] = hashlib.sha256(application_path.read_bytes()).hexdigest()
    tls_path = Path(controls["tls"]["evidence"]["path"])
    tls_path.write_text(
        json.dumps(
            {
                "schema_version": "duckdock-ga-tls-probe-v2",
                "scope": "target-production",
                "status": "PASS",
                "passed": True,
                "observed_at": observed_at.isoformat(),
                "target_environment": "customer-production",
                "source_commit": commit,
                "images": {
                    "backend": {"name": backend_image},
                    "frontend": {"name": frontend_image},
                },
                "application_url": "https://duckdock.example.com/health",
                "object_store_url": "https://objects.example.com/minio/health/live",
                "negotiated_protocols": ["TLSv1.2", "TLSv1.3"],
                "legacy_protocols_rejected": ["TLSv1", "TLSv1.1"],
                "certificate_days_remaining": 60,
                "hostname_verified": True,
                "hsts_max_age_seconds": 31_536_000,
                "endpoints": {
                    "application": {"passed": True},
                    "object_store": {"passed": True},
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    controls["tls"]["evidence"]["sha256"] = hashlib.sha256(tls_path.read_bytes()).hexdigest()
    capacity_path = Path(controls["capacity"]["evidence"]["path"])
    capacity_path.write_text(
        json.dumps(
            {
                "schema_version": "duckdock-target-capacity-gate-v2",
                "scope": "target-production",
                "status": "PASSED",
                "observed_at": observed_at.isoformat(),
                "target_environment": "customer-production",
                "source_commit": commit,
                "images": {
                    "backend": {"name": backend_image},
                    "frontend": {"name": frontend_image},
                },
                "base_url": "https://duckdock.example.com",
                "transport": "network HTTPS against target",
                "phases": [
                    {
                        "name": "sustained",
                        "duration_seconds": 900,
                        "target_rate": 50,
                        "p95_ms": 50,
                        "passed": True,
                    }
                ],
                "materialized_runs": 50_000,
                "error_rate": 0,
                "post_growth_timeline_query": {"p95_ms": 30, "passed": True},
                "passed": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    controls["capacity"]["evidence"]["sha256"] = hashlib.sha256(capacity_path.read_bytes()).hexdigest()
    ha_path = Path(controls["high_availability"]["evidence"]["path"])

    def ha_snapshot(zones: list[str]) -> dict:
        return {
            component: {
                "ready_replicas": count,
                "pods": [
                    {
                        "pod": f"{component}-{index}",
                        "node": f"node-{zone}-{index}",
                        "zone": zone,
                        "ready": True,
                    }
                    for index, zone in enumerate(
                        (zones * ((count + len(zones) - 1) // len(zones)))[:count],
                        start=1,
                    )
                ],
            }
            for component, count in {
                "backend": 3,
                "frontend": 3,
                "worker": 3,
                "beat": 1,
            }.items()
        }

    ha_path.write_text(
        json.dumps(
            {
                "schema_version": "duckdock-kubernetes-ha-failover-v2",
                "scope": "target-production",
                "status": "PASS",
                "passed": True,
                "observed_at": observed_at.isoformat(),
                "target_environment": "customer-production",
                "cluster_context": "customer-production-admin",
                "namespace": "duckdock",
                "source_commit": commit,
                "images": {
                    "backend": {"name": backend_image},
                    "frontend": {"name": frontend_image},
                },
                "fault_domains": ["zone-a", "zone-b"],
                "cluster_nodes": [
                    {
                        "node": node,
                        "zone": zone,
                        "ready": True,
                        "schedulable": True,
                        "control_plane": False,
                    }
                    for node, zone in (
                        ("node-zone-a-1", "zone-a"),
                        ("node-zone-a-3", "zone-a"),
                        ("node-zone-b-1", "zone-b"),
                        ("node-zone-b-2", "zone-b"),
                        ("node-zone-b-3", "zone-b"),
                    )
                ],
                "fault_domains_exercised": 2,
                "replica_counts": {
                    "backend": 3,
                    "frontend": 3,
                    "worker": 3,
                    "beat": 1,
                },
                "before": ha_snapshot(["zone-a", "zone-b"]),
                "after_zone_drain": ha_snapshot(["zone-b"]),
                "after_zone_return_and_rolling_rebalance": ha_snapshot(["zone-a", "zone-b"]),
                "fault_injection": {
                    "drained_nodes": ["node-zone-a-1", "node-zone-a-3"],
                    "drained_node": "node-zone-a-1",
                    "drained_zone": "zone-a",
                    "recovery_seconds": 45,
                    "node_failover_passed": True,
                    "zone_failover_passed": True,
                    "beat_recovery_passed": True,
                    "beat_original_node": "node-zone-a-1",
                    "beat_recovery_node": "node-zone-b-1",
                },
                "availability_probe": {
                    "transport": "network HTTPS against target",
                    "base_url": "https://duckdock.example.com",
                    "sample_count": 5,
                    "failure_count": 0,
                    "passed": True,
                    "samples": [
                        {
                            "observed_at": (observed_at - timedelta(seconds=5 - index)).isoformat(),
                            "passed": True,
                            "status_code": 200,
                        }
                        for index in range(5)
                    ],
                },
                "network_policy": {
                    "enforcement_exercised": True,
                    "passed": True,
                    "evidence": {},
                },
                "state_services": {
                    "managed_mysql_ha": True,
                    "managed_redis_ha": True,
                    "object_store_ha": True,
                    "rwx_repository_storage_ha": True,
                    "failover_exercised": True,
                    "data_integrity_passed": True,
                    "evidence": {},
                },
                "cleanup": {
                    "restored_nodes": ["node-zone-a-1", "node-zone-a-3"],
                    "errors": [],
                    "passed": True,
                },
                "exercise_error": None,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    controls["high_availability"]["evidence"]["sha256"] = hashlib.sha256(ha_path.read_bytes()).hexdigest()

    def write_target_report(control_name: str, report: dict) -> None:
        path = Path(controls[control_name]["evidence"]["path"])
        path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        controls[control_name]["evidence"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    def target_report(schema_version: str, status: str = "PASS") -> dict:
        return {
            "schema_version": schema_version,
            "scope": "target-production",
            "status": status,
            "passed": True,
            "observed_at": observed_at.isoformat(),
            "target_environment": "customer-production",
            "source_commit": commit,
            "images": {
                "backend": {"name": backend_image},
                "frontend": {"name": frontend_image},
            },
        }

    secrets_report = target_report("duckdock-ga-secrets-evidence-v1")
    secrets_report.update(
        {
            "provider": "External Secrets",
            "plaintext_env_persisted": False,
            "rotation_tested": True,
            "secret_store": {
                "encrypted_at_rest": True,
                "access_audit_enabled": True,
                "credentials_external_to_evidence": True,
            },
            "rotation": {
                "executed": True,
                "secret_classes": ["database", "object-store", "application-signing"],
                "started_at": (now - timedelta(minutes=20)).isoformat(),
                "completed_at": (now - timedelta(minutes=10)).isoformat(),
                "old_credentials_rejected": True,
                "workloads_reloaded": True,
                "audit_event_recorded": True,
            },
        }
    )
    write_target_report("secrets", secrets_report)

    def network_scan(host: str, ports: str, *, open_ports: list[int]) -> dict:
        states = {str(port): "open" for port in open_ports}
        if not open_ports and ports.isdigit():
            states[ports] = "filtered"
        address = host if ports != "1-65535" else "203.0.113.20"
        port_xml = "".join(
            f"<port protocol='tcp' portid='{port}'><state state='{state}'/></port>"
            for port, state in states.items()
        )
        raw_xml = (
            "<nmaprun version='7.98'><host><status state='up'/>"
            f"<address addr='{address}'/><ports>{port_xml}</ports></host>"
            "<runstats><finished elapsed='1.0'/></runstats></nmaprun>"
        )
        return {
            "host": host,
            "requested_ports": ports,
            "host_state": "up",
            "addresses": [address],
            "port_states": states,
            "open_tcp_ports": open_ports,
            "nmap_version": "7.98",
            "elapsed_seconds": 1.0,
            "raw_nmap_xml": raw_xml,
            "xml_sha256": hashlib.sha256(raw_xml.encode()).hexdigest(),
        }

    def network_connection(namespace: str, source: str, host: str, port: int, connected: bool) -> dict:
        return {
            "source_namespace": namespace,
            "source": source,
            "destination_host": host,
            "destination_port": port,
            "connected": connected,
            "kubectl_exit_code": 0 if connected else 1,
        }

    network_policies = []
    for name in (
        "allow-dns",
        "backend-from-frontend",
        "controlled-external-egress",
        "default-deny",
        "frontend-ingress-and-backend",
    ):
        spec: dict = {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
        if name == "controlled-external-egress":
            spec["egress"] = [
                {
                    "to": [{"ipBlock": {"cidr": "10.0.0.0/8"}}],
                    "ports": [{"protocol": "TCP", "port": 443}],
                }
            ]
        network_policies.append(
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

    cni_image = f"quay.io/cilium/cilium@sha256:{'e' * 64}"
    network_report = target_report("duckdock-ga-network-evidence-v2")
    network_report.update(
        {
            "public_tcp_ports": [443],
            "database_public": False,
            "redis_public": False,
            "object_store_direct_public": False,
            "default_deny_ingress": True,
            "egress_allowlist_enforced": True,
            "enforced_by": f"kube-system/cilium {cni_image}",
            "external_scan": {
                "transport": "nmap TCP scan from acknowledged external vantage",
                "scanner_id": "external-scanner-01",
                "scanner_source_ip": "8.8.8.8",
                "public_ingress": network_scan(
                    "duckdock.example.com", "1-65535", open_ports=[443]
                ),
                "private_data_services": {
                    "database": network_scan("10.0.0.10", "3306", open_ports=[]),
                    "redis": network_scan("10.0.0.11", "6379", open_ports=[]),
                    "object_store_direct": network_scan("10.0.0.12", "9000", open_ports=[]),
                },
                "discovered_tcp_ports": [443],
                "private_data_services_unreachable": True,
                "passed": True,
            },
            "policy_tests": {
                "cluster_context": "customer-production-admin",
                "namespace": "duckdock",
                "cni": {
                    "namespace": "kube-system",
                    "name": "cilium",
                    "uid": "uid-cilium",
                    "resource_version": "20",
                    "generation": 3,
                    "desired": 5,
                    "ready": 5,
                    "images": [cni_image],
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
                "network_policies": network_policies,
                "required_policies_present": True,
                "policy_snapshots_valid": True,
                "broad_world_egress_absent": True,
                "ingress_tests": {
                    "trusted_frontend_allowed": network_connection(
                        "ingress-probes",
                        "pod/probe",
                        "frontend.duckdock.svc.cluster.local",
                        8080,
                        True,
                    ),
                    "trusted_backend_allowed": network_connection(
                        "monitoring-probes",
                        "pod/probe",
                        "backend.duckdock.svc.cluster.local",
                        8801,
                        True,
                    ),
                    "untrusted_frontend_denied": network_connection(
                        "untrusted-probes",
                        "pod/probe",
                        "frontend.duckdock.svc.cluster.local",
                        8080,
                        False,
                    ),
                    "untrusted_backend_denied": network_connection(
                        "untrusted-probes",
                        "pod/probe",
                        "backend.duckdock.svc.cluster.local",
                        8801,
                        False,
                    ),
                },
                "egress_tests": {
                    "untrusted_control_destination_reachable": network_connection(
                        "untrusted-probes", "pod/probe", "control.example.com", 443, True
                    ),
                    "approved_destination_allowed": network_connection(
                        "duckdock", "deployment/backend", "mysql.internal", 3306, True
                    ),
                    "unapproved_destination_denied": network_connection(
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
    )
    write_target_report("network", network_report)

    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed target evidence verification")
    alert_delivery_identity = "alert-delivery@example.com"
    oncall_identity = "oncall-primary@example.com"
    alert_delivery_key = tmp_path / "alert_delivery_key"
    oncall_key = tmp_path / "oncall_key"
    for key in (alert_delivery_key, oncall_key):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
    alerting_allowed_signers = tmp_path / "alerting_allowed_signers"
    alerting_allowed_signers.write_text(
        (
            f"{alert_delivery_identity} "
            f"{alert_delivery_key.with_suffix('.pub').read_text(encoding='utf-8').strip()}\n"
            f"{oncall_identity} "
            f"{oncall_key.with_suffix('.pub').read_text(encoding='utf-8').strip()}\n"
        ),
        encoding="utf-8",
    )
    alerting_policy_path = tmp_path / "alerting-policy.json"
    alerting_policy = {
        "schema_version": ALERTING_POLICY_SCHEMA_VERSION,
        "policy_id": "duckdock-target-alerting-authority",
        "organization": "DuckDock Test Operations",
        "allowed_signers_path": str(alerting_allowed_signers),
        "allowed_signers_sha256": hashlib.sha256(
            alerting_allowed_signers.read_bytes()
        ).hexdigest(),
        "delivery_identities": [alert_delivery_identity],
        "oncall_schedules": {"platform-primary": [oncall_identity]},
    }
    alerting_policy_path.write_text(
        json.dumps(alerting_policy, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    exercise_id = "ga-20260806-001"
    alert_name = f"DuckDockGA_{exercise_id}"
    alert_labels = {
        "alertname": alert_name,
        "severity": "critical",
        "duckdock_target": "customer-production",
        "duckdock_exercise": exercise_id,
    }

    def signed_alert_receipt(
        filename: str,
        payload: dict,
        key: Path,
        namespace: str,
        identity: str,
    ) -> dict:
        path = tmp_path / filename
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-Y",
                "sign",
                "-f",
                str(key),
                "-n",
                namespace,
                str(path),
            ],
            check=True,
        )
        return {
            **payload,
            "signed_evidence": {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "signature_path": f"{path}.sig",
                "signer_identity": identity,
            },
        }

    exercise_started = now - timedelta(minutes=10)
    firing_expires_at = exercise_started + timedelta(minutes=15)
    firing_api_at = now - timedelta(minutes=9, seconds=50)
    active_at = now - timedelta(minutes=9, seconds=40)
    firing_delivered_at = now - timedelta(minutes=9)
    acknowledged_at = now - timedelta(minutes=8)
    resolve_requested_at = now - timedelta(minutes=7)
    resolved_api_at = now - timedelta(minutes=6, seconds=50)
    inactive_at = now - timedelta(minutes=6, seconds=40)
    resolved_delivered_at = now - timedelta(minutes=6)

    def post_observation(
        observed: datetime,
        starts_at: datetime,
        ends_at: datetime,
    ) -> dict:
        payload = [
            {
                "labels": alert_labels,
                "annotations": {"summary": "DuckDock GA signed on-call delivery exercise"},
                "startsAt": starts_at.isoformat(),
                "endsAt": ends_at.isoformat(),
            }
        ]
        raw_request = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        raw_response = "{}"
        return {
            "observed_at": observed.isoformat(),
            "http_status": 200,
            "response_bytes": len(raw_response),
            "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
            "request_sha256": hashlib.sha256(raw_request.encode()).hexdigest(),
            "raw_response_body": raw_response,
            "raw_request_body": raw_request,
            "request_payload": payload,
        }

    def state_observation(observed: datetime, *, active: bool) -> dict:
        response = [{"labels": alert_labels}] if active else []
        raw_response = json.dumps(response, sort_keys=True, separators=(",", ":"))
        selector = json.dumps(
            {"alertname": alert_name}, sort_keys=True, separators=(",", ":")
        ).encode()
        return {
            "observed_at": observed.isoformat(),
            "http_status": 200,
            "response_bytes": len(raw_response),
            "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
            "request_sha256": hashlib.sha256(b"").hexdigest(),
            "raw_response_body": raw_response,
            "raw_request_body": "",
            "matching_alerts": 1 if active else 0,
            "label_selector_sha256": hashlib.sha256(selector).hexdigest(),
            "expected_active": active,
            "passed": True,
        }

    def delivery_targets(event: str, delivered_at: datetime) -> list[dict]:
        return [
            {
                "channel": "oncall-webhook",
                "receiver": "primary-oncall",
                "provider_receipt_id": f"provider-{event}-oncall",
                "delivered_at": (delivered_at - timedelta(seconds=1)).isoformat(),
            },
            {
                "channel": "pager",
                "receiver": "primary-pager",
                "provider_receipt_id": f"provider-{event}-pager",
                "delivered_at": delivered_at.isoformat(),
            },
        ]

    firing_receipt = signed_alert_receipt(
        "alert-firing-receipt.json",
        {
            "schema_version": ALERT_DELIVERY_SCHEMA_VERSION,
            "exercise_id": exercise_id,
            "target_environment": "customer-production",
            "alert_name": alert_name,
            "event": "firing",
            "receipt_id": "incident-fire-123",
            "deliveries": delivery_targets("firing", firing_delivered_at),
            "schedule": "platform-primary",
            "delivered": True,
            "delivered_at": firing_delivered_at.isoformat(),
        },
        alert_delivery_key,
        ALERT_DELIVERY_SIGNATURE_NAMESPACE,
        alert_delivery_identity,
    )
    acknowledgement = signed_alert_receipt(
        "alert-oncall-ack.json",
        {
            "schema_version": ONCALL_ACK_SCHEMA_VERSION,
            "exercise_id": exercise_id,
            "target_environment": "customer-production",
            "alert_name": alert_name,
            "receipt_id": "incident-ack-123",
            "schedule": "platform-primary",
            "acknowledged": True,
            "acknowledged_by": oncall_identity,
            "acknowledged_at": acknowledged_at.isoformat(),
        },
        oncall_key,
        ONCALL_ACK_SIGNATURE_NAMESPACE,
        oncall_identity,
    )
    resolved_receipt = signed_alert_receipt(
        "alert-resolved-receipt.json",
        {
            "schema_version": ALERT_DELIVERY_SCHEMA_VERSION,
            "exercise_id": exercise_id,
            "target_environment": "customer-production",
            "alert_name": alert_name,
            "event": "resolved",
            "receipt_id": "incident-resolved-123",
            "deliveries": delivery_targets("resolved", resolved_delivered_at),
            "schedule": "platform-primary",
            "delivered": True,
            "delivered_at": resolved_delivered_at.isoformat(),
        },
        alert_delivery_key,
        ALERT_DELIVERY_SIGNATURE_NAMESPACE,
        alert_delivery_identity,
    )
    active_observation = state_observation(active_at, active=True)
    inactive_observation = state_observation(inactive_at, active=False)
    alerting_report = target_report(ALERTING_SCHEMA_VERSION)
    alerting_report.update(
        {
            "test_notification_delivered": True,
            "resolved_notification_delivered": True,
            "oncall_schedule": "platform-primary",
            "alerting_policy": {
                "path": str(alerting_policy_path),
                "sha256": hashlib.sha256(alerting_policy_path.read_bytes()).hexdigest(),
                "policy_id": alerting_policy["policy_id"],
                "allowed_signers_path": str(alerting_allowed_signers),
                "allowed_signers_sha256": hashlib.sha256(
                    alerting_allowed_signers.read_bytes()
                ).hexdigest(),
            },
            "alert_exercise": {
                "exercise_id": exercise_id,
                "alert_name": alert_name,
                "labels": alert_labels,
                "alertmanager_url": "https://alerts.example.com",
                "started_at": exercise_started.isoformat(),
                "firing_expires_at": firing_expires_at.isoformat(),
                "resolve_requested_at": resolve_requested_at.isoformat(),
                "firing_api": post_observation(
                    firing_api_at,
                    exercise_started,
                    firing_expires_at,
                ),
                "active_observation": active_observation,
                "resolved_api": post_observation(
                    resolved_api_at,
                    exercise_started,
                    resolve_requested_at,
                ),
                "inactive_observation": inactive_observation,
            },
            "firing_receipt": firing_receipt,
            "oncall_acknowledgement": acknowledgement,
            "resolved_receipt": resolved_receipt,
        }
    )
    write_target_report("alerting", alerting_report)

    backup_manifest_path = tmp_path / "backup-manifest.json"
    backup_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "duckdock-secure-backup-v1",
                "created_at": (now - timedelta(hours=2)).isoformat(),
                "timestamp": "20260805-080000",
                "release_commit": commit,
                "encryption": "age-x25519",
                "artifacts": {
                    name: {
                        "encrypted_sha256": digest * 64,
                        "encrypted_size_bytes": 1024,
                        "plaintext_sha256": plaintext_digest * 64,
                        "plaintext_size_bytes": 512,
                    }
                    for name, digest, plaintext_digest in (
                        ("mysql.sql.gz.age", "1", "4"),
                        ("repos.tar.gz.age", "2", "5"),
                        ("minio.tar.gz.age", "3", "6"),
                    )
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    backup_signing_key = tmp_path / "backup_signing_key"
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(backup_signing_key),
        ],
        check=True,
    )
    backup_signer_identity = "backup-operator@example.com"
    backup_allowed_signers = tmp_path / "backup_allowed_signers"
    backup_public_key = backup_signing_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    backup_allowed_signers.write_text(
        f"{backup_signer_identity} {backup_public_key}\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-Y",
            "sign",
            "-f",
            str(backup_signing_key),
            "-n",
            "duckdock-backup",
            str(backup_manifest_path),
        ],
        check=True,
    )
    backup_signature_path = Path(f"{backup_manifest_path}.sig")

    recovery_report = target_report("duckdock-ga-recovery-evidence-v1", "PASSED")
    recovery_report.update(
        {
            "offsite_media": True,
            "encrypted": True,
            "immutable_or_object_locked": True,
            "rpo_seconds": 0,
            "rto_seconds": 600,
            "mysql_rows_verified": 10,
            "objects_verified": 3,
            "git_repositories_verified": True,
            "backup": {
                "manifest_schema_version": "duckdock-secure-backup-v1",
                "path": str(backup_manifest_path),
                "manifest_sha256": hashlib.sha256(backup_manifest_path.read_bytes()).hexdigest(),
                "signer_identity": backup_signer_identity,
                "allowed_signers_path": str(backup_allowed_signers),
                "signature_path": str(backup_signature_path),
                "decryption_key_external": True,
            },
            "restore": {
                "destructive_restore": True,
                "target_environment": "customer-recovery-staging",
                "production_data_overwrite": False,
                "integrity_digest_verified": True,
                "started_at": (now - timedelta(minutes=30)).isoformat(),
                "completed_at": (now - timedelta(minutes=10)).isoformat(),
            },
            "verification": {
                "mysql_rows_verified": 10,
                "objects_verified": 3,
                "git_repositories_verified": True,
                "passed": True,
            },
        }
    )
    write_target_report("recovery", recovery_report)

    assessment_path = tmp_path / "independent-assessment-report"
    assessment_path.write_text(
        "Independent DuckDock 2.0 assessment: no open critical or high findings.\n",
        encoding="utf-8",
    )
    assessment_key = tmp_path / "independent_assessor_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(assessment_key)],
        check=True,
    )
    assessor_identity = "assessor@independent-security.example"
    assessor_allowed_signers = tmp_path / "independent_assessor_allowed_signers"
    assessor_public_key = assessment_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    assessor_allowed_signers.write_text(
        f"{assessor_identity} {assessor_public_key}\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-Y",
            "sign",
            "-f",
            str(assessment_key),
            "-n",
            "duckdock-security-assessment",
            str(assessment_path),
        ],
        check=True,
    )
    assessment_signature = assessment_path.with_suffix(".sig")

    security_report = target_report("duckdock-ga-independent-security-evidence-v1")
    security_report.update(
        {
            "independent": True,
            "provider": "Independent Security Lab",
            "contract_digest": "d" * 64,
            "open_critical": 0,
            "open_high": 0,
            "assessment": {
                "assessment_id": "ISL-DD-2026-08",
                "independence_attested": True,
                "scope": [
                    "application-and-api",
                    "identity-and-access",
                    "kubernetes-infrastructure",
                    "supply-chain",
                    "agent-security",
                ],
                "methodologies": ["penetration-test", "manual-code-review"],
                "started_at": (now - timedelta(days=14)).isoformat(),
                "completed_at": (now - timedelta(days=1)).isoformat(),
            },
            "findings": {
                "open_critical": 0,
                "open_high": 0,
                "retest_completed": True,
            },
            "signed_report": {
                "path": str(assessment_path),
                "sha256": hashlib.sha256(assessment_path.read_bytes()).hexdigest(),
                "signer_identity": assessor_identity,
                "allowed_signers_path": str(assessor_allowed_signers),
                "signature_path": str(assessment_signature),
            },
        }
    )
    write_target_report("security_assessment", security_report)

    release = {
        "version": "2.0.0",
        "git_commit": commit,
        "backend_image": backend_image,
        "frontend_image": frontend_image,
        "contract_digest": "d" * 64,
    }
    target = {
        "target_id": "customer-production",
        "environment": "production",
        "deployment_mode": "kubernetes-ha",
        "public_base_url": "https://duckdock.example.com",
        "object_store_url": "https://objects.example.com",
        "fault_domains": ["zone-a", "zone-b"],
        "maximum_rpo_seconds": 900,
        "maximum_rto_seconds": 14_400,
        "minimum_sustained_rps": 50,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "release": release,
        "target": target,
        "controls": controls,
        "approval_policy": {},
        "approvals": [],
    }


def _add_signed_approvals(document: dict, tmp_path: Path, now: datetime) -> None:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for production-approval signature verification")
    allowed_signers = tmp_path / "allowed_signers"
    public_lines: list[str] = []
    role_identities: dict[str, list[str]] = {}
    signing_keys: dict[str, Path] = {}
    for role in sorted(REQUIRED_APPROVAL_ROLES):
        identity = f"{role.lower()}@example.com"
        key = tmp_path / f"{role.lower()}_key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        public_lines.append(f"{identity} {public_key}")
        role_identities[role] = [identity]
        signing_keys[role] = key
    allowed_signers.write_text("\n".join(public_lines) + "\n", encoding="utf-8")
    policy = {
        "schema_version": APPROVAL_POLICY_SCHEMA_VERSION,
        "policy_id": "duckdock-production-release-authority",
        "organization": "DuckDock Test Release Authority",
        "allowed_signers_path": str(allowed_signers),
        "allowed_signers_sha256": hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
        "roles": role_identities,
    }
    policy_path = tmp_path / "approval-policy.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    document["approval_policy"] = {
        "policy_id": policy["policy_id"],
        "sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    }
    state_observed_at = now - timedelta(minutes=5)
    state_report_path = tmp_path / "state-services-failover.json"
    state_report = {
        "schema_version": "duckdock-ga-state-services-failover-v1",
        "scope": "target-production",
        "status": "PASS",
        "passed": True,
        "observed_at": state_observed_at.isoformat(),
        "target_environment": document["target"]["target_id"],
        "source_commit": document["release"]["git_commit"],
        "images": {
            "backend": {"name": document["release"]["backend_image"]},
            "frontend": {"name": document["release"]["frontend_image"]},
        },
        "managed_mysql_ha": True,
        "managed_redis_ha": True,
        "object_store_ha": True,
        "rwx_repository_storage_ha": True,
        "failover_exercised": True,
        "data_integrity_passed": True,
        "services": {
            name: {
                "provider": provider,
                "failover_receipt_id": f"receipt-{name}-123",
                "ha_enabled": True,
                "failover_exercised": True,
                "data_integrity_passed": True,
                "started_at": (now - timedelta(minutes=9)).isoformat(),
                "recovered_at": (now - timedelta(minutes=6)).isoformat(),
            }
            for name, provider in (
                ("mysql", "Managed MySQL"),
                ("redis", "Managed Redis"),
                ("object_store", "Managed S3"),
                ("rwx_repository_storage", "Managed RWX CSI"),
            )
        },
    }
    state_report_path.write_text(json.dumps(state_report, sort_keys=True) + "\n", encoding="utf-8")
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-Y",
            "sign",
            "-f",
            str(signing_keys["Operations"]),
            "-n",
            "duckdock-ha-state-services",
            str(state_report_path),
        ],
        check=True,
    )
    ha_evidence = document["controls"]["high_availability"]["evidence"]
    ha_report_path = Path(ha_evidence["path"])
    ha_report = json.loads(ha_report_path.read_text(encoding="utf-8"))
    network_evidence = document["controls"]["network"]["evidence"]
    ha_report["network_policy"]["evidence"] = {
        "path": network_evidence["path"],
        "sha256": network_evidence["sha256"],
    }
    ha_report["state_services"]["evidence"] = {
        "path": str(state_report_path),
        "sha256": hashlib.sha256(state_report_path.read_bytes()).hexdigest(),
        "signer_identity": role_identities["Operations"][0],
        "signature_path": str(Path(f"{state_report_path}.sig")),
        "approval_policy_id": policy["policy_id"],
        "approval_policy_sha256": document["approval_policy"]["sha256"],
    }
    ha_report_path.write_text(json.dumps(ha_report, sort_keys=True) + "\n", encoding="utf-8")
    ha_evidence["sha256"] = hashlib.sha256(ha_report_path.read_bytes()).hexdigest()
    digest = _release_digest(
        document["release"],
        document["target"],
        document["controls"],
        document["approval_policy"],
    )
    approvals: list[dict] = []
    for role in sorted(REQUIRED_APPROVAL_ROLES):
        identity = role_identities[role][0]
        key = signing_keys[role]
        approval = {
            "role": role,
            "identity": identity,
            "decision": "APPROVED",
            "approved_at": (now - timedelta(minutes=1)).isoformat(),
            "signed_digest": digest,
            "signature_path": str(tmp_path / f"{role.lower()}.sig"),
        }
        statement = tmp_path / f"{role.lower()}-statement"
        statement.write_bytes(_approval_statement(approval, digest))
        subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", "duckdock-ga", str(statement)],
            check=True,
        )
        signature = Path(approval["signature_path"])
        statement.with_suffix(".sig").replace(signature)
        approvals.append(approval)
    document["approvals"] = approvals


def test_complete_target_bundle_is_cryptographically_authorized(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)

    assert lint_authorization(document) == []
    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "GA_AUTHORIZED"
    assert result["block_count"] == 0


def test_signing_tool_emits_verifier_compatible_metadata_bound_statement(tmp_path: Path) -> None:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for production-approval signature verification")
    key = tmp_path / "product_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    identity = "product@example.com"
    allowed_signers = tmp_path / "allowed_signers_for_tool"
    allowed_signers.write_text(
        f"{identity} {key.with_suffix('.pub').read_text(encoding='utf-8').strip()}\n",
        encoding="utf-8",
    )
    digest = "d" * 64
    approved_at = "2026-08-06T10:30:00+08:00"
    signature = tmp_path / "product-tool.sig"
    subprocess.run(
        [
            str(Path(__file__).parents[2] / "scripts" / "sign-ga-approval.sh"),
            "--digest",
            digest,
            "--role",
            "Product",
            "--identity",
            identity,
            "--approved-at",
            approved_at,
            "--key",
            str(key),
            "--output",
            str(signature),
        ],
        check=True,
    )
    approval = {
        "role": "Product",
        "identity": identity,
        "decision": "APPROVED",
        "approved_at": approved_at,
        "signed_digest": digest,
        "signature_path": str(signature),
    }

    verified, detail = _verify_ssh_signature(
        identity=identity,
        allowed_signers=allowed_signers,
        signature=signature,
        approval=approval,
        release_digest=digest,
    )

    assert verified, detail


def test_approval_policy_must_be_supplied_out_of_band(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    statuses = {item["key"]: item["status"] for item in result["checks"]}
    assert statuses["approval_policy"] == "BLOCK"
    assert statuses["approval_trust_store"] == "BLOCK"


def test_tampered_shared_approval_trust_store_invalidates_all_approvals(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text(
        allowed_signers.read_text(encoding="utf-8") + "# unauthorized mutation\n",
        encoding="utf-8",
    )

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    statuses = {item["key"]: item["status"] for item in result["checks"]}
    assert statuses["approval_trust_store"] == "BLOCK"
    assert all(statuses[f"approval_{role}"] == "BLOCK" for role in REQUIRED_APPROVAL_ROLES)


def test_one_public_key_cannot_impersonate_four_distinct_approval_identities(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    allowed_signers = tmp_path / "allowed_signers"
    lines = allowed_signers.read_text(encoding="utf-8").splitlines()
    first_fields = lines[0].split()
    shared_key = f"{first_fields[1]} {first_fields[2]}"
    allowed_signers.write_text(
        "\n".join(f"{line.split()[0]} {shared_key}" for line in lines) + "\n",
        encoding="utf-8",
    )
    policy_path = tmp_path / "approval-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["allowed_signers_sha256"] = hashlib.sha256(allowed_signers.read_bytes()).hexdigest()
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    document["approval_policy"]["sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=policy_path,
        now=now,
    )

    statuses = {item["key"]: item["status"] for item in result["checks"]}
    assert statuses["approval_policy"] == "PASS"
    assert statuses["approval_trust_store"] == "BLOCK"


def test_valid_signature_from_wrong_policy_role_cannot_approve(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    product = next(item for item in document["approvals"] if item["role"] == "Product")
    security = next(item for item in document["approvals"] if item["role"] == "Security")
    product["identity"], security["identity"] = security["identity"], product["identity"]
    for approval, key_role in ((product, "Security"), (security, "Product")):
        statement = tmp_path / f"wrong-role-{approval['role'].lower()}-statement"
        statement.write_bytes(_approval_statement(approval, approval["signed_digest"]))
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-Y",
                "sign",
                "-f",
                str(tmp_path / f"{key_role.lower()}_key"),
                "-n",
                "duckdock-ga",
                str(statement),
            ],
            check=True,
        )
        statement.with_suffix(".sig").replace(Path(approval["signature_path"]))

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "AWAITING_EXTERNAL_APPROVALS"
    four_eyes = next(item for item in result["checks"] if item["key"] == "approval_four_eyes")
    product_check = next(item for item in result["checks"] if item["key"] == "approval_Product")
    security_check = next(item for item in result["checks"] if item["key"] == "approval_Security")
    assert four_eyes["status"] == "PASS"
    assert product_check["status"] == "BLOCK"
    assert security_check["status"] == "BLOCK"
    assert "role_authorized=False" in product_check["observed"]
    assert "role_authorized=False" in security_check["observed"]


def test_unsigned_approval_timestamp_edit_invalidates_signature(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    product = next(item for item in document["approvals"] if item["role"] == "Product")
    product["approved_at"] = (now - timedelta(seconds=30)).isoformat()

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    product_check = next(item for item in result["checks"] if item["key"] == "approval_Product")
    assert product_check["status"] == "BLOCK"
    assert "Signature verification failed" in product_check["observed"]


def test_v2_rejects_per_approval_trust_store_override(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    document["approvals"][0]["allowed_signers_path"] = str(tmp_path / "attacker-trust-store")

    assert lint_authorization(document) == [
        "approvals[0].allowed_signers_path is forbidden; use the release-authority approval policy"
    ]


def test_internal_security_claim_is_not_independent_authorization(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    document["controls"]["security_assessment"]["independent"] = False

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "AWAITING_EXTERNAL_APPROVALS"
    check = next(item for item in result["checks"] if item["key"] == "security_assessment")
    assert check["status"] == "BLOCK"


def test_raw_readiness_response_without_target_binding_cannot_authorize_ga(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["application_readiness"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "schema_version",
        "scope",
        "passed",
        "observed_at",
        "target_environment",
        "source_commit",
        "images",
        "base_url",
        "transport",
    ):
        report.pop(key)
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    readiness = next(item for item in result["checks"] if item["key"] == "application_readiness")
    assert readiness["status"] == "BLOCK"


def test_readiness_report_from_previous_release_cannot_authorize_current_release(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["application_readiness"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["images"]["backend"]["name"] = f"registry.example.com/duckdock/backend@sha256:{'9' * 64}"
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    readiness = next(item for item in result["checks"] if item["key"] == "application_readiness")
    assert readiness["status"] == "BLOCK"


def test_unrestricted_target_network_blocks_ga(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    document["controls"]["network"]["egress_allowlist_enforced"] = False

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    assert result["block_count"] == 1


@pytest.mark.parametrize("mutation", ["hidden_public_port", "unreachable_control", "broad_egress_spec"])
def test_network_gate_revalidates_raw_evidence_instead_of_trusting_summaries(
    tmp_path: Path,
    mutation: str,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["network"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "hidden_public_port":
        public_scan = report["external_scan"]["public_ingress"]
        public_scan["raw_nmap_xml"] = public_scan["raw_nmap_xml"].replace(
            "</ports>",
            "<port protocol='tcp' portid='8443'><state state='open'/></port></ports>",
        )
        public_scan["xml_sha256"] = hashlib.sha256(
            public_scan["raw_nmap_xml"].encode()
        ).hexdigest()
    elif mutation == "unreachable_control":
        control = report["policy_tests"]["egress_tests"]["untrusted_control_destination_reachable"]
        control["connected"] = False
        control["kubectl_exit_code"] = 1
    else:
        policy = next(
            item
            for item in report["policy_tests"]["network_policies"]
            if item["name"] == "controlled-external-egress"
        )
        policy["spec"]["egress"][0]["to"][0]["ipBlock"]["cidr"] = "0.0.0.0/0"
        policy["spec_sha256"] = hashlib.sha256(
            json.dumps(policy["spec"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    network_check = next(item for item in result["checks"] if item["key"] == "network")
    assert network_check["status"] == "BLOCK"


@pytest.mark.parametrize(
    "mutation",
    [
        "tampered_delivery_receipt",
        "delivery_key_used_for_ack",
        "wildcard_oncall_trust",
        "forged_active_summary",
    ],
)
def test_alerting_gate_revalidates_signed_receipts_and_exact_identity_roles(
    tmp_path: Path,
    mutation: str,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["alerting"]["evidence"]
    report_path = Path(evidence["path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if mutation == "tampered_delivery_receipt":
        firing_path = Path(report["firing_receipt"]["signed_evidence"]["path"])
        firing = json.loads(firing_path.read_text(encoding="utf-8"))
        firing["delivered"] = False
        firing_path.write_text(json.dumps(firing, sort_keys=True) + "\n", encoding="utf-8")
    elif mutation == "delivery_key_used_for_ack":
        embedded_ack = report["oncall_acknowledgement"]
        signed_ack = embedded_ack["signed_evidence"]
        ack_path = Path(signed_ack["path"])
        ack = json.loads(ack_path.read_text(encoding="utf-8"))
        ack["acknowledged_by"] = "alert-delivery@example.com"
        ack_path.write_text(json.dumps(ack, sort_keys=True) + "\n", encoding="utf-8")
        signature_path = Path(signed_ack["signature_path"])
        signature_path.unlink()
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-Y",
                "sign",
                "-f",
                str(tmp_path / "alert_delivery_key"),
                "-n",
                ONCALL_ACK_SIGNATURE_NAMESPACE,
                str(ack_path),
            ],
            check=True,
        )
        report["oncall_acknowledgement"] = {
            **ack,
            "signed_evidence": {
                **signed_ack,
                "sha256": hashlib.sha256(ack_path.read_bytes()).hexdigest(),
                "signer_identity": "alert-delivery@example.com",
            },
        }
        report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        evidence["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    elif mutation == "wildcard_oncall_trust":
        policy_reference = report["alerting_policy"]
        policy_path = Path(policy_reference["path"])
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        trust_path = Path(policy["allowed_signers_path"])
        trust = trust_path.read_text(encoding="utf-8").replace(
            "oncall-primary@example.com ",
            "oncall-*@example.com ",
        )
        trust_path.write_text(trust, encoding="utf-8")
        trust_digest = hashlib.sha256(trust_path.read_bytes()).hexdigest()
        policy["allowed_signers_sha256"] = trust_digest
        policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
        policy_reference["allowed_signers_sha256"] = trust_digest
        policy_reference["sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        evidence["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    else:
        active = report["alert_exercise"]["active_observation"]
        active["raw_response_body"] = "[]"
        active["response_bytes"] = 2
        active["response_sha256"] = hashlib.sha256(b"[]").hexdigest()
        report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        evidence["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    alerting_check = next(item for item in result["checks"] if item["key"] == "alerting")
    assert alerting_check["status"] == "BLOCK"


@pytest.mark.parametrize(
    ("control_name", "expected_status"),
    [
        ("secrets", "BLOCKED"),
        ("network", "BLOCKED"),
        ("alerting", "BLOCKED"),
        ("recovery", "BLOCKED"),
        ("security_assessment", "AWAITING_EXTERNAL_APPROVALS"),
    ],
)
def test_unrelated_digest_bound_json_cannot_substitute_for_target_control_evidence(
    tmp_path: Path,
    control_name: str,
    expected_status: str,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"][control_name]["evidence"]
    path = Path(evidence["path"])
    path.write_text(
        json.dumps({"control": control_name, "passed": True}) + "\n",
        encoding="utf-8",
    )
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == expected_status
    check = next(item for item in result["checks"] if item["key"] == control_name)
    assert check["status"] == "BLOCK"


def test_target_control_reports_cannot_be_reused_for_another_environment(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    bound_controls = (
        "secrets",
        "network",
        "alerting",
        "recovery",
        "security_assessment",
    )
    for control_name in bound_controls:
        evidence = document["controls"][control_name]["evidence"]
        path = Path(evidence["path"])
        report = json.loads(path.read_text(encoding="utf-8"))
        report["target_environment"] = "different-production"
        path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    statuses = {item["key"]: item["status"] for item in result["checks"]}
    assert all(statuses[control_name] == "BLOCK" for control_name in bound_controls)


def test_secrets_evidence_rejects_embedded_secret_material(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["secrets"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["rotation"]["password"] = "must-not-be-retained"
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    check = next(item for item in result["checks"] if item["key"] == "secrets")
    assert check["status"] == "BLOCK"


def test_tampered_backup_manifest_fails_even_when_digest_claim_is_updated(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["recovery"]["evidence"]
    evidence_path = Path(evidence["path"])
    report = json.loads(evidence_path.read_text(encoding="utf-8"))
    manifest_path = Path(report["backup"]["path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["mysql.sql.gz.age"]["plaintext_size_bytes"] = 999
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    report["backup"]["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    evidence_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    signature_check = next(item for item in result["checks"] if item["key"] == "recovery_backup_signature")
    assert signature_check["status"] == "BLOCK"


def test_tampered_assessor_report_fails_even_when_digest_claim_is_updated(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["security_assessment"]["evidence"]
    evidence_path = Path(evidence["path"])
    report = json.loads(evidence_path.read_text(encoding="utf-8"))
    signed_report_path = Path(report["signed_report"]["path"])
    signed_report_path.write_text("tampered after assessor signature\n", encoding="utf-8")
    report["signed_report"]["sha256"] = hashlib.sha256(signed_report_path.read_bytes()).hexdigest()
    evidence_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "AWAITING_EXTERNAL_APPROVALS"
    signature_check = next(item for item in result["checks"] if item["key"] == "security_assessment_signature")
    assert signature_check["status"] == "BLOCK"


def test_local_asgi_capacity_report_cannot_authorize_target(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["capacity"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["schema_version"] = "duckdock-capacity-gate-v1"
    report["transport"] = "FastAPI ASGI HTTP with real MySQL"
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    capacity = next(item for item in result["checks"] if item["key"] == "capacity")
    assert capacity["status"] == "BLOCK"


def test_tls_report_from_previous_release_cannot_authorize_current_release(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["tls"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["source_commit"] = "9" * 40
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    tls = next(item for item in result["checks"] if item["key"] == "tls")
    assert tls["status"] == "BLOCK"


def test_capacity_report_from_previous_image_cannot_authorize_current_release(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["capacity"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["images"]["frontend"]["name"] = f"registry.example.com/duckdock/frontend@sha256:{'9' * 64}"
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    capacity = next(item for item in result["checks"] if item["key"] == "capacity")
    assert capacity["status"] == "BLOCK"


def test_local_reference_ha_report_cannot_authorize_target(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["high_availability"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["scope"] = "local-rehearsal"
    report["target_environment"] = "local-kind-duckdock-ga-ha"
    report["availability_probe"]["transport"] = (
        "in-cluster frontend Service health and unauthenticated API proxy probes"
    )
    report["network_policy"]["enforcement_exercised"] = False
    report["state_services"]["managed_mysql_ha"] = False
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert high_availability["status"] == "BLOCK"


def test_ha_claims_cannot_outpace_fault_injection_evidence(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["high_availability"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["fault_injection"]["zone_failover_passed"] = False
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert high_availability["status"] == "BLOCK"


def test_ha_ready_count_must_match_retained_pod_snapshot(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    evidence = document["controls"]["high_availability"]["evidence"]
    path = Path(evidence["path"])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["after_zone_drain"]["backend"]["pods"].pop()
    path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _add_signed_approvals(document, tmp_path, now)

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    assert result["status"] == "BLOCKED"
    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert high_availability["status"] == "BLOCK"


def test_ha_state_service_receipt_tamper_fails_operations_signature(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    ha_evidence = document["controls"]["high_availability"]["evidence"]
    ha_path = Path(ha_evidence["path"])
    ha_report = json.loads(ha_path.read_text(encoding="utf-8"))
    state_reference = ha_report["state_services"]["evidence"]
    state_path = Path(state_reference["path"])
    state_report = json.loads(state_path.read_text(encoding="utf-8"))
    state_report["services"]["mysql"]["failover_receipt_id"] = "tampered-receipt"
    state_path.write_text(json.dumps(state_report, sort_keys=True) + "\n", encoding="utf-8")
    state_reference["sha256"] = hashlib.sha256(state_path.read_bytes()).hexdigest()
    ha_path.write_text(json.dumps(ha_report, sort_keys=True) + "\n", encoding="utf-8")
    ha_evidence["sha256"] = hashlib.sha256(ha_path.read_bytes()).hexdigest()

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    signature = next(
        item for item in result["checks"] if item["key"] == "high_availability_state_services_signature"
    )
    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert signature["status"] == "BLOCK"
    assert high_availability["status"] == "BLOCK"


def test_ha_network_claim_must_bind_the_network_control_evidence(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    ha_evidence = document["controls"]["high_availability"]["evidence"]
    ha_path = Path(ha_evidence["path"])
    ha_report = json.loads(ha_path.read_text(encoding="utf-8"))
    ha_report["network_policy"]["evidence"]["sha256"] = "f" * 64
    ha_path.write_text(json.dumps(ha_report, sort_keys=True) + "\n", encoding="utf-8")
    ha_evidence["sha256"] = hashlib.sha256(ha_path.read_bytes()).hexdigest()

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert high_availability["status"] == "BLOCK"
    assert "network=False" in high_availability["observed"]


def test_ha_summary_cannot_hide_a_failed_https_probe_sample(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    ha_evidence = document["controls"]["high_availability"]["evidence"]
    ha_path = Path(ha_evidence["path"])
    ha_report = json.loads(ha_path.read_text(encoding="utf-8"))
    ha_report["availability_probe"]["samples"][2]["passed"] = False
    ha_report["availability_probe"]["samples"][2]["status_code"] = 503
    ha_path.write_text(json.dumps(ha_report, sort_keys=True) + "\n", encoding="utf-8")
    ha_evidence["sha256"] = hashlib.sha256(ha_path.read_bytes()).hexdigest()

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert high_availability["status"] == "BLOCK"
    assert "probe=False" in high_availability["observed"]


def test_ha_report_requires_successful_zone_cleanup(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    ha_evidence = document["controls"]["high_availability"]["evidence"]
    ha_path = Path(ha_evidence["path"])
    ha_report = json.loads(ha_path.read_text(encoding="utf-8"))
    ha_report["cleanup"] = {
        "restored_nodes": [],
        "errors": ["uncordon failed"],
        "passed": False,
    }
    ha_path.write_text(json.dumps(ha_report, sort_keys=True) + "\n", encoding="utf-8")
    ha_evidence["sha256"] = hashlib.sha256(ha_path.read_bytes()).hexdigest()

    result = evaluate(
        document,
        authorization_path=tmp_path / "authorization.json",
        approval_policy_path=tmp_path / "approval-policy.json",
        now=now,
    )

    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert high_availability["status"] == "BLOCK"
    assert "cleanup=False" in high_availability["observed"]
