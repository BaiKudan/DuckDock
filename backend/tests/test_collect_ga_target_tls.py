from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_target_tls as target_tls
from scripts.ga_release_identity import build_release_binding
from scripts.ga_tls_evidence import (
    TLS_POLICY_SCHEMA_VERSION,
    TLS_RAW_SCHEMA_VERSION,
    TLS_SIGNATURE_NAMESPACE,
    canonical_digest,
)


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
TARGET = "customer-production"
APPLICATION_URL = "https://duckdock.example.com/health"
OBJECT_STORE_URL = "https://objects.example.com/minio/health/live"
EXERCISE_ID = "tls-ga-20260806"
PROBE_ID = "external-tls-probe-01"
VANTAGE_ID = "internet-hangzhou-01"
SOURCE_IP = "1.1.1.1"
SIGNER_IDENTITY = "tls-probe@example.com"


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
            TLS_SIGNATURE_NAMESPACE,
            str(path),
        ],
        check=True,
    )
    return Path(f"{path}.sig")


def _handshake(protocol: str, captured_at: datetime, certificate: str) -> dict:
    expires_at = captured_at + timedelta(days=60)
    return {
        "ok": True,
        "captured_at": captured_at.isoformat(),
        "protocol": protocol,
        "cipher": "TLS_AES_256_GCM_SHA384",
        "peer_ip": "203.0.113.10",
        "peer_certificate_sha256": certificate,
        "certificate_serial_number": "01ABCD",
        "certificate_subject": "commonName=duckdock.example.com",
        "certificate_issuer": "commonName=Example Public CA",
        "certificate_not_before": (captured_at - timedelta(days=1)).isoformat(),
        "expires_at": expires_at.isoformat(),
        "certificate_days_remaining": 60,
        "hostname_verified": True,
    }


def _http(observed_at: datetime) -> dict:
    headers = [
        {"name": "content-type", "value": "application/json"},
        {
            "name": "strict-transport-security",
            "value": "max-age=31536000; includeSubDomains",
        },
    ]
    body = b'{"status":"ok"}'
    return {
        "ok": True,
        "observed_at": observed_at.isoformat(),
        "status_code": 200,
        "hsts": "max-age=31536000; includeSubDomains",
        "hsts_max_age_seconds": 31_536_000,
        "response_headers": headers,
        "response_headers_sha256": canonical_digest(headers),
        "body_prefix_bytes": len(body),
        "body_prefix_sha256": hashlib.sha256(body).hexdigest(),
    }


def _legacy() -> dict:
    output = "legacy protocol rejected by target\n"
    return {
        "rejected": True,
        "openssl_exit_code": 1,
        "result": "rejected",
        "raw_output": output,
        "raw_output_sha256": hashlib.sha256(output.encode()).hexdigest(),
    }


def _endpoint(
    *,
    host: str,
    path: str,
    captured_at: datetime,
    certificate: str,
) -> dict:
    return {
        "host": host,
        "port": 443,
        "path": path,
        "handshakes": {
            "TLSv1.2": _handshake("TLSv1.2", captured_at, certificate),
            "TLSv1.3": _handshake(
                "TLSv1.3",
                captured_at + timedelta(seconds=1),
                certificate,
            ),
        },
        "http": _http(captured_at + timedelta(seconds=2)),
        "legacy_protocols": {"TLSv1": _legacy(), "TLSv1.1": _legacy()},
        "passed": True,
    }


def _raw_report(now: datetime) -> dict:
    captured_at = now - timedelta(minutes=2)
    return {
        "schema_version": TLS_RAW_SCHEMA_VERSION,
        "scope": "target-production",
        "target_environment": TARGET,
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
        "status": "PASS",
        "passed": True,
        "observed_at": (captured_at + timedelta(seconds=3)).isoformat(),
        "exercise_id": EXERCISE_ID,
        "probe": {
            "probe_id": PROBE_ID,
            "vantage_id": VANTAGE_ID,
            "vantage_class": "external-internet",
            "source_ip": SOURCE_IP,
        },
        "application_url": APPLICATION_URL,
        "object_store_url": OBJECT_STORE_URL,
        "negotiated_protocols": ["TLSv1.2", "TLSv1.3"],
        "certificate_days_remaining": 60,
        "hostname_verified": True,
        "legacy_protocols_rejected": ["TLSv1", "TLSv1.1"],
        "hsts_max_age_seconds": 31_536_000,
        "minimum_certificate_days": 30,
        "minimum_hsts_max_age_seconds": 31_536_000,
        "endpoints": {
            "application": _endpoint(
                host="duckdock.example.com",
                path="/health",
                captured_at=captured_at,
                certificate="1" * 64,
            ),
            "object_store": _endpoint(
                host="objects.example.com",
                path="/minio/health/live",
                captured_at=captured_at,
                certificate="2" * 64,
            ),
        },
    }


def _material(
    tmp_path: Path,
    *,
    source_cidr: str = "1.1.1.0/24",
    extra_identity_same_key: bool = False,
) -> SimpleNamespace:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed TLS evidence tests")
    key = tmp_path / "tls_probe_key"
    _generate_key(key)
    trust_path = tmp_path / "tls.allowed-signers"
    public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    identities = [SIGNER_IDENTITY]
    trust = f"{SIGNER_IDENTITY} {public_key}\n"
    if extra_identity_same_key:
        identities.append("second-probe@example.com")
        trust += f"second-probe@example.com {public_key}\n"
    trust_path.write_text(trust, encoding="utf-8")
    policy = {
        "schema_version": TLS_POLICY_SCHEMA_VERSION,
        "policy_id": "duckdock-target-tls-authority",
        "organization": "DuckDock Test Security",
        "allowed_signers_path": str(trust_path),
        "allowed_signers_sha256": hashlib.sha256(trust_path.read_bytes()).hexdigest(),
        "probe_operator_identities": identities,
        "approved_probe_ids": [PROBE_ID],
        "approved_vantage_ids": [VANTAGE_ID],
        "approved_vantage_classes": ["external-internet"],
        "approved_source_cidrs": [source_cidr],
    }
    policy_path = tmp_path / "tls-policy.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    raw_path = tmp_path / "tls-raw.json"
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
        application_url=APPLICATION_URL,
        object_store_url=OBJECT_STORE_URL,
        exercise_id=EXERCISE_ID,
        tls_policy=policy_path,
        probe_signer_identity=SIGNER_IDENTITY,
        probe_report=raw_path,
        probe_signature=signature,
        output=tmp_path / "tls-evidence.json",
        overwrite_output=False,
        release_binding=build_release_binding(
            scope="target-production",
            target_environment=TARGET,
            source_commit=COMMIT,
            backend_image=BACKEND_IMAGE,
            frontend_image=FRONTEND_IMAGE,
        ),
    )


def test_collects_signed_external_tls_evidence(tmp_path: Path) -> None:
    args = _material(tmp_path)

    report = target_tls.collect(args)

    assert report["status"] == "PASS"
    assert report["probe"]["source_ip"] == SOURCE_IP
    assert report["certificate_days_remaining"] == 60
    assert report["hsts_max_age_seconds"] == 31_536_000
    assert report["signed_probe"]["signed_evidence"]["signer_identity"] == SIGNER_IDENTITY


def test_rejects_probe_modified_after_signature(tmp_path: Path) -> None:
    args = _material(tmp_path)
    raw = json.loads(args.probe_report.read_text(encoding="utf-8"))
    raw["endpoints"]["application"]["http"]["status_code"] = 204
    args.probe_report.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid signed TLS probe"):
        target_tls.collect(args)


def test_rejects_source_outside_policy_cidr(tmp_path: Path) -> None:
    args = _material(tmp_path, source_cidr="8.8.8.0/24")

    with pytest.raises(ValueError, match="does not authorize"):
        target_tls.collect(args)


def test_rejects_public_key_reuse_across_probe_identities(tmp_path: Path) -> None:
    args = _material(tmp_path, extra_identity_same_key=True)

    with pytest.raises(ValueError, match="reuses a public key"):
        target_tls.collect(args)


def test_rejects_credential_fields_even_when_legitimately_signed(tmp_path: Path) -> None:
    args = _material(tmp_path)
    raw = json.loads(args.probe_report.read_text(encoding="utf-8"))
    raw["password"] = "must-not-be-retained"
    args.probe_report.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")
    args.probe_signature.unlink()
    _sign(args.probe_report, tmp_path / "tls_probe_key")

    with pytest.raises(ValueError, match="not a release-bound"):
        target_tls.collect(args)
