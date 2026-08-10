"""Strict contracts shared by the target TLS probe, collector and GA gate."""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse


TLS_RAW_SCHEMA_VERSION = "duckdock-ga-tls-probe-v3"
TLS_EVIDENCE_SCHEMA_VERSION = "duckdock-ga-tls-evidence-v3"
TLS_POLICY_SCHEMA_VERSION = "duckdock-ga-tls-trust-policy-v1"
TLS_SIGNATURE_NAMESPACE = "duckdock-tls-probe-report"
EXERCISE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
HSTS_MAX_AGE_RE = re.compile(r"(?:^|;)\s*max-age=(\d+)", re.IGNORECASE)
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")


def meaningful(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(marker in value for marker in PLACEHOLDER_MARKERS)
    )


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def strict_int(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def finite_number(value: Any, *, minimum: float = 0) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= minimum
    )


def contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "access_key",
        "api_key",
        "client_secret",
        "credential",
        "credential_value",
        "password",
        "password_value",
        "plaintext",
        "plaintext_value",
        "private_key",
        "raw_secret",
        "secret_value",
        "session_token",
        "token",
        "token_value",
    }
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden or contains_secret_material_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_secret_material_key(item) for item in value)
    return False


def canonical_digest(value: Any) -> str:
    payload = json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def json_bytes(value: Any) -> bytes:
    import json

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def https_endpoint(value: Any) -> tuple[str, int, str] | None:
    if not meaningful(value):
        return None
    parsed = urlparse(str(value))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return parsed.hostname, parsed.port or 443, path


def globally_routable_ip(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def _release_binding_valid(
    value: dict[str, Any],
    *,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
) -> bool:
    images = value.get("images")
    return (
        value.get("scope") == "target-production"
        and value.get("target_environment") == target_environment
        and value.get("source_commit") == source_commit
        and isinstance(images, dict)
        and set(images) == {"backend", "frontend"}
        and all(
            isinstance(images.get(component), dict)
            and set(images[component]) == {"name"}
            for component in ("backend", "frontend")
        )
        and images["backend"].get("name") == backend_image
        and images["frontend"].get("name") == frontend_image
    )


def _handshake_valid(value: Any, *, expected_protocol: str) -> dict[str, Any] | None:
    expected_keys = {
        "ok",
        "captured_at",
        "protocol",
        "cipher",
        "peer_ip",
        "peer_certificate_sha256",
        "certificate_serial_number",
        "certificate_subject",
        "certificate_issuer",
        "certificate_not_before",
        "expires_at",
        "certificate_days_remaining",
        "hostname_verified",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return None
    captured_at = parse_time(value.get("captured_at"))
    not_before = parse_time(value.get("certificate_not_before"))
    expires_at = parse_time(value.get("expires_at"))
    try:
        peer_ip = ipaddress.ip_address(str(value.get("peer_ip", "")))
    except ValueError:
        return None
    if not (
        value.get("ok") is True
        and captured_at is not None
        and value.get("protocol") == expected_protocol
        and meaningful(value.get("cipher"))
        and peer_ip is not None
        and bool(DIGEST_RE.fullmatch(str(value.get("peer_certificate_sha256", ""))))
        and meaningful(value.get("certificate_serial_number"))
        and meaningful(value.get("certificate_subject"))
        and meaningful(value.get("certificate_issuer"))
        and not_before is not None
        and expires_at is not None
        and not_before <= captured_at < expires_at
        and strict_int(value.get("certificate_days_remaining"))
        and value.get("hostname_verified") is True
    ):
        return None
    derived_days = int((expires_at - captured_at).total_seconds() // 86_400)
    if value["certificate_days_remaining"] != derived_days:
        return None
    return {
        "captured_at": captured_at,
        "expires_at": expires_at,
        "certificate_days_remaining": derived_days,
        "certificate_sha256": value["peer_certificate_sha256"],
    }


def _http_valid(value: Any) -> dict[str, Any] | None:
    expected_keys = {
        "ok",
        "observed_at",
        "status_code",
        "hsts",
        "hsts_max_age_seconds",
        "response_headers",
        "response_headers_sha256",
        "body_prefix_bytes",
        "body_prefix_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return None
    observed_at = parse_time(value.get("observed_at"))
    hsts = value.get("hsts")
    headers = value.get("response_headers")
    match = HSTS_MAX_AGE_RE.search(hsts) if isinstance(hsts, str) else None
    derived_hsts = int(match.group(1)) if match else 0
    header_hsts = [
        item.get("value")
        for item in headers or []
        if isinstance(item, dict) and item.get("name") == "strict-transport-security"
    ]
    if not (
        value.get("ok") is True
        and observed_at is not None
        and strict_int(value.get("status_code"), minimum=200)
        and value["status_code"] < 400
        and isinstance(hsts, str)
        and isinstance(headers, list)
        and all(
            isinstance(item, dict)
            and set(item) == {"name", "value"}
            and meaningful(item.get("name"))
            and isinstance(item.get("value"), str)
            for item in headers
        )
        and header_hsts == [hsts]
        and strict_int(value.get("hsts_max_age_seconds"))
        and value["hsts_max_age_seconds"] == derived_hsts
        and value.get("response_headers_sha256") == canonical_digest(headers)
        and strict_int(value.get("body_prefix_bytes"))
        and value["body_prefix_bytes"] <= 4096
        and bool(DIGEST_RE.fullmatch(str(value.get("body_prefix_sha256", ""))))
    ):
        return None
    return {"observed_at": observed_at, "hsts_max_age_seconds": derived_hsts}


def _legacy_valid(value: Any) -> bool:
    expected_keys = {
        "rejected",
        "openssl_exit_code",
        "result",
        "raw_output",
        "raw_output_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return False
    raw_output = value.get("raw_output")
    if not isinstance(raw_output, str) or len(raw_output.encode("utf-8")) > 16_384:
        return False
    digest = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
    negotiated = value.get("openssl_exit_code") == 0 and "Protocol version:" in raw_output
    return (
        isinstance(value.get("openssl_exit_code"), int)
        and not isinstance(value.get("openssl_exit_code"), bool)
        and value.get("rejected") is (not negotiated)
        and value.get("result") == ("negotiated" if negotiated else "rejected")
        and value.get("raw_output_sha256") == digest
        and value.get("rejected") is True
    )


def _endpoint_valid(
    value: Any,
    *,
    expected_endpoint: tuple[str, int, str],
) -> dict[str, Any] | None:
    expected_keys = {
        "host",
        "port",
        "path",
        "handshakes",
        "http",
        "legacy_protocols",
        "passed",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return None
    host, port, path = expected_endpoint
    handshakes = value.get("handshakes")
    legacy = value.get("legacy_protocols")
    if not (
        value.get("host") == host
        and value.get("port") == port
        and value.get("path") == path
        and isinstance(handshakes, dict)
        and set(handshakes) == {"TLSv1.2", "TLSv1.3"}
        and isinstance(legacy, dict)
        and set(legacy) == {"TLSv1", "TLSv1.1"}
        and value.get("passed") is True
    ):
        return None
    tls12 = _handshake_valid(handshakes["TLSv1.2"], expected_protocol="TLSv1.2")
    tls13 = _handshake_valid(handshakes["TLSv1.3"], expected_protocol="TLSv1.3")
    http = _http_valid(value.get("http"))
    if not (
        tls12
        and tls13
        and http
        and _legacy_valid(legacy["TLSv1"])
        and _legacy_valid(legacy["TLSv1.1"])
    ):
        return None
    observed_times = [tls12["captured_at"], tls13["captured_at"], http["observed_at"]]
    return {
        "observed_at": max(observed_times),
        "certificate_days_remaining": min(
            tls12["certificate_days_remaining"],
            tls13["certificate_days_remaining"],
        ),
        "hsts_max_age_seconds": http["hsts_max_age_seconds"],
        "protocols": {"TLSv1.2", "TLSv1.3"},
    }


def validate_tls_probe_report(
    report: Any,
    *,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    application_url: str,
    object_store_url: str,
    exercise_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("TLS probe report must be a JSON object")
    expected_keys = {
        "schema_version",
        "scope",
        "target_environment",
        "source_commit",
        "images",
        "status",
        "passed",
        "observed_at",
        "exercise_id",
        "probe",
        "application_url",
        "object_store_url",
        "negotiated_protocols",
        "certificate_days_remaining",
        "hostname_verified",
        "legacy_protocols_rejected",
        "hsts_max_age_seconds",
        "minimum_certificate_days",
        "minimum_hsts_max_age_seconds",
        "endpoints",
    }
    current = now or datetime.now(timezone.utc)
    observed_at = parse_time(report.get("observed_at"))
    probe = report.get("probe")
    endpoints = report.get("endpoints")
    app_endpoint = https_endpoint(application_url)
    object_endpoint = https_endpoint(object_store_url)
    if not (
        set(report) == expected_keys
        and report.get("schema_version") == TLS_RAW_SCHEMA_VERSION
        and _release_binding_valid(
            report,
            target_environment=target_environment,
            source_commit=source_commit,
            backend_image=backend_image,
            frontend_image=frontend_image,
        )
        and report.get("status") == "PASS"
        and report.get("passed") is True
        and observed_at is not None
        and observed_at <= current
        and report.get("exercise_id") == exercise_id
        and bool(EXERCISE_RE.fullmatch(exercise_id))
        and isinstance(probe, dict)
        and set(probe) == {"probe_id", "vantage_id", "vantage_class", "source_ip"}
        and meaningful(probe.get("probe_id"))
        and meaningful(probe.get("vantage_id"))
        and probe.get("vantage_class") == "external-internet"
        and globally_routable_ip(probe.get("source_ip"))
        and report.get("application_url") == application_url
        and report.get("object_store_url") == object_store_url
        and app_endpoint is not None
        and object_endpoint is not None
        and isinstance(endpoints, dict)
        and set(endpoints) == {"application", "object_store"}
        and strict_int(report.get("minimum_certificate_days"), minimum=30)
        and strict_int(report.get("minimum_hsts_max_age_seconds"), minimum=31_536_000)
        and not contains_secret_material_key(report)
    ):
        raise ValueError("TLS probe report is not a release-bound external target exercise")
    app_result = _endpoint_valid(endpoints["application"], expected_endpoint=app_endpoint)
    object_result = _endpoint_valid(endpoints["object_store"], expected_endpoint=object_endpoint)
    if not app_result or not object_result:
        raise ValueError("TLS endpoint raw observations are incomplete or inconsistent")
    last_raw_observation = max(app_result["observed_at"], object_result["observed_at"])
    minimum_days = min(
        app_result["certificate_days_remaining"],
        object_result["certificate_days_remaining"],
    )
    minimum_hsts = min(
        app_result["hsts_max_age_seconds"],
        object_result["hsts_max_age_seconds"],
    )
    if not (
        last_raw_observation <= observed_at
        and observed_at - last_raw_observation <= timedelta(minutes=5)
        and report.get("negotiated_protocols") == ["TLSv1.2", "TLSv1.3"]
        and report.get("legacy_protocols_rejected") == ["TLSv1", "TLSv1.1"]
        and report.get("certificate_days_remaining") == minimum_days
        and report.get("hostname_verified") is True
        and report.get("hsts_max_age_seconds") == minimum_hsts
        and minimum_days >= report["minimum_certificate_days"]
        and minimum_hsts >= report["minimum_hsts_max_age_seconds"]
    ):
        raise ValueError("TLS probe summary does not match raw observations or thresholds")
    return {
        "observed_at": observed_at,
        "probe": probe,
        "negotiated_protocols": ["TLSv1.2", "TLSv1.3"],
        "legacy_protocols_rejected": ["TLSv1", "TLSv1.1"],
        "certificate_days_remaining": minimum_days,
        "hostname_verified": True,
        "hsts_max_age_seconds": minimum_hsts,
    }
