from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.verify_ga_production_authorization import (
    REQUIRED_APPROVAL_ROLES,
    SCHEMA_VERSION,
    _release_digest,
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
                "status": "READY",
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
                        "node": f"node-{zone}",
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
                "schema_version": "duckdock-kubernetes-ha-failover-v1",
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
                "fault_domains": ["zone-a", "zone-b"],
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
                    "drained_node": "node-zone-a",
                    "drained_zone": "zone-a",
                    "recovery_seconds": 45,
                    "node_failover_passed": True,
                    "zone_failover_passed": True,
                    "beat_recovery_passed": True,
                    "beat_original_node": "node-zone-a",
                    "beat_recovery_node": "node-zone-b",
                },
                "availability_probe": {
                    "transport": "network HTTPS against target",
                    "base_url": "https://duckdock.example.com",
                    "sample_count": 60,
                    "failure_count": 0,
                    "passed": True,
                },
                "network_policy": {
                    "enforcement_exercised": True,
                    "passed": True,
                },
                "state_services": {
                    "managed_mysql_ha": True,
                    "managed_redis_ha": True,
                    "object_store_ha": True,
                    "rwx_repository_storage_ha": True,
                    "failover_exercised": True,
                    "data_integrity_passed": True,
                },
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

    network_report = target_report("duckdock-ga-network-evidence-v1")
    network_report.update(
        {
            "public_tcp_ports": [443],
            "database_public": False,
            "redis_public": False,
            "object_store_direct_public": False,
            "default_deny_ingress": True,
            "egress_allowlist_enforced": True,
            "enforced_by": "Cilium 1.18",
            "external_scan": {
                "transport": "network TCP scan from outside target",
                "discovered_tcp_ports": [443],
                "passed": True,
            },
            "policy_tests": {
                "default_deny_ingress_exercised": True,
                "unapproved_egress_denied": True,
                "approved_egress_allowed": True,
                "private_data_services_unreachable_externally": True,
                "passed": True,
            },
        }
    )
    write_target_report("network", network_report)

    alerting_report = target_report("duckdock-ga-alerting-evidence-v1")
    alerting_report.update(
        {
            "test_notification_delivered": True,
            "resolved_notification_delivered": True,
            "oncall_schedule": "platform-primary",
            "firing_receipt": {
                "receipt_id": "incident-fire-123",
                "delivered": True,
                "delivered_at": (now - timedelta(minutes=8)).isoformat(),
            },
            "oncall_acknowledgement": {
                "receipt_id": "incident-ack-123",
                "acknowledged": True,
                "schedule": "platform-primary",
                "acknowledged_at": (now - timedelta(minutes=7)).isoformat(),
            },
            "resolved_receipt": {
                "receipt_id": "incident-resolved-123",
                "delivered": True,
                "delivered_at": (now - timedelta(minutes=6)).isoformat(),
            },
        }
    )
    write_target_report("alerting", alerting_report)

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
                "manifest_sha256": "e" * 64,
                "signature_verified": True,
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

    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for independent-assessment verification")
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
        "approvals": [],
    }


def _add_signed_approvals(document: dict, tmp_path: Path, now: datetime) -> None:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for production-approval signature verification")
    digest = _release_digest(document["release"], document["target"], document["controls"])
    statement = tmp_path / "statement"
    statement.write_text(f"{SCHEMA_VERSION}:{digest}\n", encoding="utf-8")
    allowed_signers = tmp_path / "allowed_signers"
    public_lines: list[str] = []
    approvals: list[dict] = []
    for role in sorted(REQUIRED_APPROVAL_ROLES):
        identity = f"{role.lower()}@example.com"
        key = tmp_path / f"{role.lower()}_key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        public_lines.append(f"{identity} {public_key}")
        subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", "duckdock-ga", str(statement)],
            check=True,
        )
        signature = tmp_path / f"{role.lower()}.sig"
        statement.with_suffix(".sig").replace(signature)
        approvals.append(
            {
                "role": role,
                "identity": identity,
                "decision": "APPROVED",
                "approved_at": (now - timedelta(minutes=1)).isoformat(),
                "signed_digest": digest,
                "allowed_signers_path": str(allowed_signers),
                "signature_path": str(signature),
            }
        )
    allowed_signers.write_text("\n".join(public_lines) + "\n", encoding="utf-8")
    document["approvals"] = approvals


def test_complete_target_bundle_is_cryptographically_authorized(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)

    assert lint_authorization(document) == []
    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

    assert result["status"] == "GA_AUTHORIZED"
    assert result["block_count"] == 0


def test_internal_security_claim_is_not_independent_authorization(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    document["controls"]["security_assessment"]["independent"] = False

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

    assert result["status"] == "AWAITING_EXTERNAL_APPROVALS"
    check = next(item for item in result["checks"] if item["key"] == "security_assessment")
    assert check["status"] == "BLOCK"


def test_unrestricted_target_network_blocks_ga(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    document = _document(tmp_path, now)
    _add_signed_approvals(document, tmp_path, now)
    document["controls"]["network"]["egress_allowlist_enforced"] = False

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

    assert result["status"] == "BLOCKED"
    assert result["block_count"] == 1


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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

    assert result["status"] == "BLOCKED"
    check = next(item for item in result["checks"] if item["key"] == "secrets")
    assert check["status"] == "BLOCK"


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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

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

    result = evaluate(document, authorization_path=tmp_path / "authorization.json", now=now)

    assert result["status"] == "BLOCKED"
    high_availability = next(item for item in result["checks"] if item["key"] == "high_availability")
    assert high_availability["status"] == "BLOCK"
