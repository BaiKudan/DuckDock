from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_state_services_ha as collector
from scripts.ga_release_identity import build_release_binding
from scripts.ga_state_services_evidence import (
    PROVIDER_RECEIPT_SCHEMA_VERSION,
    PROVIDER_SIGNATURE_NAMESPACE,
    REQUIRED_SERVICES,
    STATE_POLICY_SCHEMA_VERSION,
    VERIFICATION_RECEIPT_SCHEMA_VERSION,
    VERIFICATION_SIGNATURE_NAMESPACE,
)


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
TARGET = "customer-production"
EXERCISE_ID = "state-ha-20260806"
PROVIDER_IDENTITY = "state-provider@example.com"
VERIFIER_IDENTITY = "state-verifier@example.com"
PROVIDERS = {
    "mysql": "Managed MySQL",
    "redis": "Managed Redis",
    "object_store": "Managed S3",
    "rwx_repository_storage": "Managed RWX CSI",
}


def _key(path: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        check=True,
    )


def _sign(path: Path, key: Path, namespace: str) -> Path:
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", namespace, str(path)],
        check=True,
    )
    return Path(f"{path}.sig")


def _binding_fields() -> dict:
    return {
        "target_environment": TARGET,
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
    }


def _receipts(now: datetime) -> tuple[dict, dict]:
    provider_services = {}
    verification_services = {}
    for name, provider in PROVIDERS.items():
        event_id = f"event-{name}-123"
        provider_services[name] = {
            "provider": provider,
            "service_instance_id": f"instance-{name}-prod",
            "topology_id": f"topology-{name}-multi-zone",
            "fault_domain_count": 3,
            "failover_event_id": event_id,
            "source_fault_domain": "zone-a",
            "destination_fault_domain": "zone-b",
            "ha_enabled": True,
            "automatic_failover": True,
            "started_at": (now - timedelta(minutes=12)).isoformat(),
            "recovered_at": (now - timedelta(minutes=8)).isoformat(),
            "provider_event_sha256": hashlib.sha256(f"event-{name}".encode()).hexdigest(),
        }
        existing = hashlib.sha256(f"existing-{name}".encode()).hexdigest()
        write = hashlib.sha256(f"write-{name}".encode()).hexdigest()
        verification_services[name] = {
            "failover_event_id": event_id,
            "verification_id": f"verify-{name}-123",
            "verified_at": (now - timedelta(minutes=2)).isoformat(),
            "pre_failover_data_sha256": existing,
            "post_failover_data_sha256": existing,
            "write_probe_sha256": write,
            "read_back_sha256": write,
            "read_probe_passed": True,
            "write_probe_passed": True,
            "data_integrity_passed": True,
            "verification_log_sha256": hashlib.sha256(f"log-{name}".encode()).hexdigest(),
        }
    return (
        {
            "schema_version": PROVIDER_RECEIPT_SCHEMA_VERSION,
            **_binding_fields(),
            "exercise_id": EXERCISE_ID,
            "observed_at": (now - timedelta(minutes=7)).isoformat(),
            "services": provider_services,
        },
        {
            "schema_version": VERIFICATION_RECEIPT_SCHEMA_VERSION,
            **_binding_fields(),
            "exercise_id": EXERCISE_ID,
            "observed_at": (now - timedelta(minutes=1)).isoformat(),
            "services": verification_services,
        },
    )


def _material(
    tmp_path: Path,
    *,
    shared_key: bool = False,
    approved_mysql_provider: str = "Managed MySQL",
) -> SimpleNamespace:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed state-services evidence tests")
    provider_key = tmp_path / "provider_key"
    verifier_key = provider_key if shared_key else tmp_path / "verifier_key"
    _key(provider_key)
    if not shared_key:
        _key(verifier_key)
    trust = tmp_path / "state.allowed-signers"
    trust.write_text(
        (
            f"{PROVIDER_IDENTITY} "
            f"{provider_key.with_suffix('.pub').read_text(encoding='utf-8').strip()}\n"
            f"{VERIFIER_IDENTITY} "
            f"{verifier_key.with_suffix('.pub').read_text(encoding='utf-8').strip()}\n"
        ),
        encoding="utf-8",
    )
    policy = {
        "schema_version": STATE_POLICY_SCHEMA_VERSION,
        "policy_id": "duckdock-state-services-authority",
        "organization": "DuckDock Test Architecture",
        "allowed_signers_path": str(trust),
        "allowed_signers_sha256": hashlib.sha256(trust.read_bytes()).hexdigest(),
        "provider_identities": [PROVIDER_IDENTITY],
        "verifier_identities": [VERIFIER_IDENTITY],
        "required_services": list(REQUIRED_SERVICES),
        "approved_providers": {
            **{name: [provider] for name, provider in PROVIDERS.items()},
            "mysql": [approved_mysql_provider],
        },
    }
    policy_path = tmp_path / "state-policy.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    provider, verification = _receipts(datetime.now(timezone.utc))
    provider_path = tmp_path / "provider.json"
    verification_path = tmp_path / "verification.json"
    provider_path.write_text(json.dumps(provider, sort_keys=True) + "\n", encoding="utf-8")
    verification_path.write_text(
        json.dumps(verification, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    provider_signature = _sign(provider_path, provider_key, PROVIDER_SIGNATURE_NAMESPACE)
    verifier_signature = _sign(
        verification_path,
        verifier_key,
        VERIFICATION_SIGNATURE_NAMESPACE,
    )
    return SimpleNamespace(
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        exercise_id=EXERCISE_ID,
        state_services_policy=policy_path,
        provider_signer_identity=PROVIDER_IDENTITY,
        verifier_signer_identity=VERIFIER_IDENTITY,
        provider_receipt=provider_path,
        provider_signature=provider_signature,
        verification_receipt=verification_path,
        verification_signature=verifier_signature,
        output=tmp_path / "state-evidence.json",
        overwrite_output=False,
        release_binding=build_release_binding(
            scope="target-production",
            target_environment=TARGET,
            source_commit=COMMIT,
            backend_image=BACKEND_IMAGE,
            frontend_image=FRONTEND_IMAGE,
        ),
    )


def _resign(path: Path, signature: Path, key: Path, namespace: str) -> None:
    signature.unlink()
    _sign(path, key, namespace)


def test_collects_provider_and_independent_verifier_state_evidence(tmp_path: Path) -> None:
    report = collector.collect(_material(tmp_path))

    assert report["status"] == "PASS"
    assert set(report["services"]) == set(REQUIRED_SERVICES)
    assert report["services"]["mysql"]["fault_domain_count"] == 3
    assert report["services"]["mysql"]["read_probe_passed"] is True
    assert (
        report["provider_receipt"]["signed_evidence"]["signer_identity"]
        != report["verification_receipt"]["signed_evidence"]["signer_identity"]
    )


def test_rejects_provider_receipt_modified_after_signature(tmp_path: Path) -> None:
    args = _material(tmp_path)
    receipt = json.loads(args.provider_receipt.read_text(encoding="utf-8"))
    receipt["services"]["mysql"]["destination_fault_domain"] = "zone-c"
    args.provider_receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid signed"):
        collector.collect(args)


def test_rejects_unapproved_provider_even_when_legitimately_signed(tmp_path: Path) -> None:
    args = _material(tmp_path, approved_mysql_provider="Different Managed MySQL")

    with pytest.raises(ValueError, match="invalid mysql"):
        collector.collect(args)


def test_rejects_shared_public_key_between_provider_and_verifier(tmp_path: Path) -> None:
    args = _material(tmp_path, shared_key=True)

    with pytest.raises(ValueError, match="reuses a public key"):
        collector.collect(args)


def test_rejects_verifier_forged_integrity_digest_even_when_resigned(tmp_path: Path) -> None:
    args = _material(tmp_path)
    receipt = json.loads(args.verification_receipt.read_text(encoding="utf-8"))
    receipt["services"]["mysql"]["post_failover_data_sha256"] = "f" * 64
    args.verification_receipt.write_text(
        json.dumps(receipt, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _resign(
        args.verification_receipt,
        args.verification_signature,
        tmp_path / "verifier_key",
        VERIFICATION_SIGNATURE_NAMESPACE,
    )

    with pytest.raises(ValueError, match="invalid mysql integrity"):
        collector.collect(args)


def test_rejects_credentials_even_when_provider_receipt_is_resigned(tmp_path: Path) -> None:
    args = _material(tmp_path)
    receipt = json.loads(args.provider_receipt.read_text(encoding="utf-8"))
    receipt["password"] = "must-not-be-retained"
    args.provider_receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    _resign(
        args.provider_receipt,
        args.provider_signature,
        tmp_path / "provider_key",
        PROVIDER_SIGNATURE_NAMESPACE,
    )

    with pytest.raises(ValueError, match="release-bound"):
        collector.collect(args)
