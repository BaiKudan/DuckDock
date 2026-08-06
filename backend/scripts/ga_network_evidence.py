"""Strict contracts shared by target network probing, collection and GA verification."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from typing import Any


NETWORK_RAW_SCHEMA_VERSION = "duckdock-ga-network-probe-v3"
NETWORK_EVIDENCE_SCHEMA_VERSION = "duckdock-ga-network-evidence-v3"
NETWORK_POLICY_SCHEMA_VERSION = "duckdock-ga-network-trust-policy-v1"
NETWORK_SIGNATURE_NAMESPACE = "duckdock-network-probe-report"
EXERCISE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
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


def globally_routable_ip(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "access_key",
        "api_key",
        "authorization",
        "bearer_token",
        "client_secret",
        "credential",
        "credential_value",
        "kubeconfig",
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


def validate_network_probe_envelope(
    report: dict[str, Any],
    *,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    exercise_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate immutable identity/binding fields before raw observations are recomputed."""

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
        "public_tcp_ports",
        "database_public",
        "redis_public",
        "object_store_direct_public",
        "default_deny_ingress",
        "egress_allowlist_enforced",
        "enforced_by",
        "external_scan",
        "policy_tests",
    }
    probe = report.get("probe")
    observed_at = parse_time(report.get("observed_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not (
        set(report) == expected_keys
        and report.get("schema_version") == NETWORK_RAW_SCHEMA_VERSION
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
        and observed_at <= current.replace(microsecond=current.microsecond)
        and report.get("exercise_id") == exercise_id
        and EXERCISE_RE.fullmatch(exercise_id)
        and isinstance(probe, dict)
        and set(probe) == {"probe_id", "vantage_id", "vantage_class", "source_ip"}
        and meaningful(probe.get("probe_id"))
        and meaningful(probe.get("vantage_id"))
        and probe.get("vantage_class") == "external-internet"
        and globally_routable_ip(probe.get("source_ip"))
        and report.get("public_tcp_ports") == [443]
        and report.get("database_public") is False
        and report.get("redis_public") is False
        and report.get("object_store_direct_public") is False
        and report.get("default_deny_ingress") is True
        and report.get("egress_allowlist_enforced") is True
        and meaningful(report.get("enforced_by"))
        and isinstance(report.get("external_scan"), dict)
        and report["external_scan"].get("scanner_id") == probe.get("probe_id")
        and report["external_scan"].get("scanner_source_ip") == probe.get("source_ip")
        and isinstance(report.get("policy_tests"), dict)
        and meaningful(report["policy_tests"].get("cluster_context"))
        and meaningful(report["policy_tests"].get("namespace"))
        and not contains_secret_material_key(report)
    ):
        raise ValueError("network probe is not a release-bound external PASS report")
    return {"observed_at": observed_at, "probe": probe}
