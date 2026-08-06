from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_target_network_evidence as collector
from scripts.ga_network_evidence import (
    NETWORK_POLICY_SCHEMA_VERSION,
    NETWORK_RAW_SCHEMA_VERSION,
    NETWORK_SIGNATURE_NAMESPACE,
)
from scripts.ga_release_identity import build_release_binding


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
TARGET = "customer-production"
EXERCISE_ID = "network-ga-20260806"
PROBE_ID = "external-network-probe-01"
VANTAGE_ID = "internet-hangzhou-01"
SOURCE_IP = "1.1.1.1"
SIGNER_IDENTITY = "network-probe@example.com"


def _generate_key(path: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        check=True,
    )


def _sign(path: Path, key: Path) -> Path:
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            NETWORK_SIGNATURE_NAMESPACE,
            str(path),
        ],
        check=True,
    )
    return Path(f"{path}.sig")


def _raw_report(now: datetime) -> dict:
    return {
        "schema_version": NETWORK_RAW_SCHEMA_VERSION,
        "scope": "target-production",
        "target_environment": TARGET,
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
        "status": "PASS",
        "passed": True,
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "exercise_id": EXERCISE_ID,
        "probe": {
            "probe_id": PROBE_ID,
            "vantage_id": VANTAGE_ID,
            "vantage_class": "external-internet",
            "source_ip": SOURCE_IP,
        },
        "public_tcp_ports": [443],
        "database_public": False,
        "redis_public": False,
        "object_store_direct_public": False,
        "default_deny_ingress": True,
        "egress_allowlist_enforced": True,
        "enforced_by": f"kube-system/cilium quay.io/cilium/cilium@sha256:{'d' * 64}",
        "external_scan": {
            "scanner_id": PROBE_ID,
            "scanner_source_ip": SOURCE_IP,
        },
        "policy_tests": {
            "cluster_context": "customer-production-admin",
            "namespace": "duckdock",
            "cni": {"namespace": "kube-system", "name": "cilium"},
        },
    }


def _material(
    tmp_path: Path,
    *,
    source_cidr: str = "1.1.1.0/24",
    extra_identity_same_key: bool = False,
) -> SimpleNamespace:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed network evidence tests")
    key = tmp_path / "network_probe_key"
    _generate_key(key)
    trust = tmp_path / "network.allowed-signers"
    public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    identities = [SIGNER_IDENTITY]
    trust_text = f"{SIGNER_IDENTITY} {public_key}\n"
    if extra_identity_same_key:
        identities.append("second-network-probe@example.com")
        trust_text += f"second-network-probe@example.com {public_key}\n"
    trust.write_text(trust_text, encoding="utf-8")
    policy = {
        "schema_version": NETWORK_POLICY_SCHEMA_VERSION,
        "policy_id": "duckdock-target-network-authority",
        "organization": "DuckDock Test Security",
        "allowed_signers_path": str(trust),
        "allowed_signers_sha256": hashlib.sha256(trust.read_bytes()).hexdigest(),
        "probe_operator_identities": identities,
        "approved_probe_ids": [PROBE_ID],
        "approved_vantage_ids": [VANTAGE_ID],
        "approved_vantage_classes": ["external-internet"],
        "approved_source_cidrs": [source_cidr],
        "approved_cluster_contexts": ["customer-production-admin"],
        "approved_namespaces": ["duckdock"],
        "approved_cni_daemonsets": ["kube-system/cilium"],
    }
    policy_path = tmp_path / "network-policy.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    raw_path = tmp_path / "network-raw.json"
    raw_path.write_text(
        json.dumps(_raw_report(datetime.now(timezone.utc)), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    signature = _sign(raw_path, key)
    return SimpleNamespace(
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        exercise_id=EXERCISE_ID,
        network_policy=policy_path,
        probe_signer_identity=SIGNER_IDENTITY,
        probe_report=raw_path,
        probe_signature=signature,
        output=tmp_path / "network-evidence.json",
        overwrite_output=False,
        release_binding=build_release_binding(
            scope="target-production",
            target_environment=TARGET,
            source_commit=COMMIT,
            backend_image=BACKEND_IMAGE,
            frontend_image=FRONTEND_IMAGE,
        ),
    )


def test_collects_signed_external_network_evidence(tmp_path: Path) -> None:
    args = _material(tmp_path)

    report = collector.collect(args)

    assert report["status"] == "PASS"
    assert report["probe"]["source_ip"] == SOURCE_IP
    assert report["policy_tests"]["cluster_context"] == "customer-production-admin"
    assert report["signed_probe"]["signed_evidence"]["signer_identity"] == SIGNER_IDENTITY


def test_rejects_probe_modified_after_signature(tmp_path: Path) -> None:
    args = _material(tmp_path)
    raw = json.loads(args.probe_report.read_text(encoding="utf-8"))
    raw["public_tcp_ports"] = [443, 8443]
    args.probe_report.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="external PASS|invalid signed"):
        collector.collect(args)


def test_rejects_source_outside_policy_cidr(tmp_path: Path) -> None:
    args = _material(tmp_path, source_cidr="8.8.8.0/24")

    with pytest.raises(ValueError, match="does not authorize"):
        collector.collect(args)


def test_rejects_unapproved_cluster_context_even_with_valid_signature(tmp_path: Path) -> None:
    args = _material(tmp_path)
    raw = json.loads(args.probe_report.read_text(encoding="utf-8"))
    raw["policy_tests"]["cluster_context"] = "attacker-cluster-admin"
    args.probe_report.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")
    args.probe_signature.unlink()
    _sign(args.probe_report, tmp_path / "network_probe_key")

    with pytest.raises(ValueError, match="does not authorize"):
        collector.collect(args)


def test_rejects_public_key_reuse_across_probe_identities(tmp_path: Path) -> None:
    args = _material(tmp_path, extra_identity_same_key=True)

    with pytest.raises(ValueError, match="reuses a public key"):
        collector.collect(args)


def test_rejects_credentials_even_when_legitimately_resigned(tmp_path: Path) -> None:
    args = _material(tmp_path)
    raw = json.loads(args.probe_report.read_text(encoding="utf-8"))
    raw["kubeconfig"] = "must-not-be-retained"
    args.probe_report.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")
    args.probe_signature.unlink()
    _sign(args.probe_report, tmp_path / "network_probe_key")

    with pytest.raises(ValueError, match="external PASS"):
        collector.collect(args)
