from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from scripts.g2_target_capacity_gate import parse_args as parse_capacity_args
from scripts.ga_release_identity import build_release_binding
import scripts.probe_ga_target_tls as tls_probe
from scripts.ga_tls_evidence import canonical_digest, validate_tls_probe_report
from scripts.probe_ga_target_tls import parse_args as parse_tls_args


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
TLS_PROBE_ARGS = [
    "--exercise-id",
    "tls-ga-20260806",
    "--probe-id",
    "external-tls-probe-01",
    "--vantage-id",
    "internet-hangzhou-01",
    "--source-ip",
    "1.1.1.1",
    "--acknowledge-external-vantage",
    "customer-production",
]


def test_release_binding_requires_immutable_commit_images_and_named_target() -> None:
    binding = build_release_binding(
        scope="target-production",
        target_environment="customer-production",
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
    )

    assert binding == {
        "scope": "target-production",
        "target_environment": "customer-production",
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
    }

    with pytest.raises(ValueError, match="40-character"):
        build_release_binding(
            scope="target-production",
            target_environment="customer-production",
            source_commit="main",
            backend_image=BACKEND_IMAGE,
            frontend_image=FRONTEND_IMAGE,
        )
    with pytest.raises(ValueError, match="backend image"):
        build_release_binding(
            scope="target-production",
            target_environment="customer-production",
            source_commit=COMMIT,
            backend_image="duckdock/backend:latest",
            frontend_image=FRONTEND_IMAGE,
        )


def test_tls_probe_cli_builds_the_release_binding() -> None:
    args = parse_tls_args(
        [
            "--app-url",
            "https://duckdock.example.com/health",
            "--object-store-url",
            "https://objects.example.com/minio/health/live",
            "--target-environment",
            "customer-production",
            "--source-commit",
            COMMIT,
            "--backend-image",
            BACKEND_IMAGE,
            "--frontend-image",
            FRONTEND_IMAGE,
            *TLS_PROBE_ARGS,
        ]
    )

    assert args.release_binding["source_commit"] == COMMIT
    assert args.release_binding["scope"] == "target-production"


def test_tls_probe_v3_retains_recomputable_release_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = parse_tls_args(
        [
            "--app-url",
            "https://duckdock.example.com/health",
            "--object-store-url",
            "https://objects.example.com/minio/health/live",
            "--target-environment",
            "customer-production",
            "--source-commit",
            COMMIT,
            "--backend-image",
            BACKEND_IMAGE,
            "--frontend-image",
            FRONTEND_IMAGE,
            *TLS_PROBE_ARGS,
        ]
    )

    def fake_handshake(_host, _port, *, version, **_kwargs):
        protocol = "TLSv1.2" if version == tls_probe.ssl.TLSVersion.TLSv1_2 else "TLSv1.3"
        captured_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        return {
            "ok": True,
            "captured_at": captured_at.isoformat(),
            "protocol": protocol,
            "cipher": "TLS_AES_256_GCM_SHA384",
            "peer_ip": "203.0.113.10",
            "peer_certificate_sha256": "1" * 64,
            "certificate_serial_number": "01ABCD",
            "certificate_subject": "commonName=duckdock.example.com",
            "certificate_issuer": "commonName=Example Public CA",
            "certificate_not_before": (captured_at - timedelta(days=1)).isoformat(),
            "expires_at": (captured_at + timedelta(days=60)).isoformat(),
            "certificate_days_remaining": 60,
            "hostname_verified": True,
        }

    monkeypatch.setattr(tls_probe, "_handshake", fake_handshake)
    monkeypatch.setattr(
        tls_probe,
        "_http_probe",
        lambda *_args, **_kwargs: _fake_tls_http_result(),
    )
    monkeypatch.setattr(
        tls_probe,
        "_legacy_protocol_rejected",
        lambda *_args, **_kwargs: _fake_legacy_rejection(),
    )

    report = tls_probe.probe(args)

    assert report["schema_version"] == "duckdock-ga-tls-probe-v3"
    assert report["status"] == "PASS"
    assert report["passed"] is True
    assert report["target_environment"] == "customer-production"
    assert report["source_commit"] == COMMIT
    assert report["images"]["frontend"]["name"] == FRONTEND_IMAGE
    derived = validate_tls_probe_report(
        report,
        target_environment="customer-production",
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        application_url="https://duckdock.example.com/health",
        object_store_url="https://objects.example.com/minio/health/live",
        exercise_id="tls-ga-20260806",
    )
    assert derived["certificate_days_remaining"] == 60


def _fake_tls_http_result() -> dict:
    headers = [
        {
            "name": "strict-transport-security",
            "value": "max-age=31536000; includeSubDomains",
        }
    ]
    body = b'{"status":"ok"}'
    return {
        "ok": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status_code": 200,
        "hsts": "max-age=31536000; includeSubDomains",
        "hsts_max_age_seconds": 31_536_000,
        "response_headers": headers,
        "response_headers_sha256": canonical_digest(headers),
        "body_prefix_bytes": len(body),
        "body_prefix_sha256": hashlib.sha256(body).hexdigest(),
    }


def _fake_legacy_rejection() -> dict:
    output = "legacy protocol rejected\n"
    return {
        "rejected": True,
        "openssl_exit_code": 1,
        "result": "rejected",
        "raw_output": output,
        "raw_output_sha256": hashlib.sha256(output.encode()).hexdigest(),
    }


def test_capacity_cli_rejects_local_http_under_production_scope() -> None:
    common = [
        "--base-url",
        "http://127.0.0.1:8801",
        "--target-environment",
        "local-capacity",
        "--acknowledge-target-mutation",
        "local-capacity",
        "--namespace-id",
        "1",
        "--exercise-id",
        "capacity-local-validation",
        "--source-commit",
        COMMIT,
        "--backend-image",
        BACKEND_IMAGE,
        "--frontend-image",
        FRONTEND_IMAGE,
        "--allow-http-localhost",
    ]

    with pytest.raises(SystemExit):
        parse_capacity_args(common)

    args = parse_capacity_args([*common, "--scope", "local-validation"])
    assert args.release_binding["scope"] == "local-validation"
