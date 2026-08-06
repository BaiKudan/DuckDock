"""Strict contracts for independently verified managed state-service HA evidence."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


STATE_EVIDENCE_SCHEMA_VERSION = "duckdock-ga-state-services-failover-v2"
STATE_POLICY_SCHEMA_VERSION = "duckdock-ga-state-services-trust-policy-v1"
PROVIDER_RECEIPT_SCHEMA_VERSION = "duckdock-ga-state-services-provider-receipt-v1"
VERIFICATION_RECEIPT_SCHEMA_VERSION = "duckdock-ga-state-services-verification-receipt-v1"
PROVIDER_SIGNATURE_NAMESPACE = "duckdock-ha-provider-failover-receipt"
VERIFICATION_SIGNATURE_NAMESPACE = "duckdock-ha-state-verification-receipt"
OPERATIONS_SIGNATURE_NAMESPACE = "duckdock-ha-state-services"
REQUIRED_SERVICES = ("mysql", "redis", "object_store", "rwx_repository_storage")
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


def contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "access_key",
        "api_key",
        "authorization",
        "client_secret",
        "credential",
        "credential_value",
        "database_url",
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
        value.get("target_environment") == target_environment
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


def validate_provider_receipt(
    receipt: Any,
    *,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    exercise_id: str,
    approved_providers: dict[str, set[str]],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise ValueError("state-services provider receipt must be an object")
    expected_keys = {
        "schema_version",
        "target_environment",
        "source_commit",
        "images",
        "exercise_id",
        "observed_at",
        "services",
    }
    observed_at = parse_time(receipt.get("observed_at"))
    current = now or datetime.now(timezone.utc)
    services = receipt.get("services")
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == PROVIDER_RECEIPT_SCHEMA_VERSION
        and _release_binding_valid(
            receipt,
            target_environment=target_environment,
            source_commit=source_commit,
            backend_image=backend_image,
            frontend_image=frontend_image,
        )
        and receipt.get("exercise_id") == exercise_id
        and bool(EXERCISE_RE.fullmatch(exercise_id))
        and observed_at is not None
        and observed_at <= current
        and isinstance(services, dict)
        and set(services) == set(REQUIRED_SERVICES)
        and set(approved_providers) == set(REQUIRED_SERVICES)
        and not contains_secret_material_key(receipt)
    ):
        raise ValueError("provider receipt is not release-bound state-service evidence")
    derived: dict[str, Any] = {}
    for name in REQUIRED_SERVICES:
        service = services[name]
        expected_service_keys = {
            "provider",
            "service_instance_id",
            "topology_id",
            "fault_domain_count",
            "failover_event_id",
            "source_fault_domain",
            "destination_fault_domain",
            "ha_enabled",
            "automatic_failover",
            "started_at",
            "recovered_at",
            "provider_event_sha256",
        }
        started_at = parse_time(service.get("started_at")) if isinstance(service, dict) else None
        recovered_at = parse_time(service.get("recovered_at")) if isinstance(service, dict) else None
        if not (
            isinstance(service, dict)
            and set(service) == expected_service_keys
            and service.get("provider") in approved_providers[name]
            and meaningful(service.get("service_instance_id"))
            and meaningful(service.get("topology_id"))
            and isinstance(service.get("fault_domain_count"), int)
            and not isinstance(service.get("fault_domain_count"), bool)
            and service["fault_domain_count"] >= 2
            and meaningful(service.get("failover_event_id"))
            and meaningful(service.get("source_fault_domain"))
            and meaningful(service.get("destination_fault_domain"))
            and service["source_fault_domain"] != service["destination_fault_domain"]
            and service.get("ha_enabled") is True
            and service.get("automatic_failover") is True
            and started_at is not None
            and recovered_at is not None
            and started_at < recovered_at <= observed_at
            and recovered_at - started_at <= timedelta(hours=4)
            and observed_at - recovered_at <= timedelta(minutes=5)
            and bool(DIGEST_RE.fullmatch(str(service.get("provider_event_sha256", ""))))
        ):
            raise ValueError(f"provider receipt has invalid {name} failover evidence")
        derived[name] = {
            **service,
            "started_at_parsed": started_at,
            "recovered_at_parsed": recovered_at,
        }
    return {"observed_at": observed_at, "services": derived}


def validate_verification_receipt(
    receipt: Any,
    *,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    exercise_id: str,
    provider_services: dict[str, dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise ValueError("state-services verification receipt must be an object")
    expected_keys = {
        "schema_version",
        "target_environment",
        "source_commit",
        "images",
        "exercise_id",
        "observed_at",
        "services",
    }
    observed_at = parse_time(receipt.get("observed_at"))
    current = now or datetime.now(timezone.utc)
    services = receipt.get("services")
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == VERIFICATION_RECEIPT_SCHEMA_VERSION
        and _release_binding_valid(
            receipt,
            target_environment=target_environment,
            source_commit=source_commit,
            backend_image=backend_image,
            frontend_image=frontend_image,
        )
        and receipt.get("exercise_id") == exercise_id
        and bool(EXERCISE_RE.fullmatch(exercise_id))
        and observed_at is not None
        and observed_at <= current
        and isinstance(services, dict)
        and set(services) == set(REQUIRED_SERVICES)
        and set(provider_services) == set(REQUIRED_SERVICES)
        and not contains_secret_material_key(receipt)
    ):
        raise ValueError("verification receipt is not release-bound state-service evidence")
    derived: dict[str, Any] = {}
    for name in REQUIRED_SERVICES:
        service = services[name]
        provider = provider_services[name]
        expected_service_keys = {
            "failover_event_id",
            "verification_id",
            "verified_at",
            "pre_failover_data_sha256",
            "post_failover_data_sha256",
            "write_probe_sha256",
            "read_back_sha256",
            "read_probe_passed",
            "write_probe_passed",
            "data_integrity_passed",
            "verification_log_sha256",
        }
        verified_at = parse_time(service.get("verified_at")) if isinstance(service, dict) else None
        if not (
            isinstance(service, dict)
            and set(service) == expected_service_keys
            and service.get("failover_event_id") == provider.get("failover_event_id")
            and meaningful(service.get("verification_id"))
            and verified_at is not None
            and provider["recovered_at_parsed"] <= verified_at <= observed_at
            and verified_at - provider["recovered_at_parsed"] <= timedelta(hours=4)
            and observed_at - verified_at <= timedelta(minutes=5)
            and all(
                bool(DIGEST_RE.fullmatch(str(service.get(key, ""))))
                for key in (
                    "pre_failover_data_sha256",
                    "post_failover_data_sha256",
                    "write_probe_sha256",
                    "read_back_sha256",
                    "verification_log_sha256",
                )
            )
            and service["pre_failover_data_sha256"] == service["post_failover_data_sha256"]
            and service["write_probe_sha256"] == service["read_back_sha256"]
            and service.get("read_probe_passed") is True
            and service.get("write_probe_passed") is True
            and service.get("data_integrity_passed") is True
        ):
            raise ValueError(f"verification receipt has invalid {name} integrity evidence")
        derived[name] = {**service, "verified_at_parsed": verified_at}
    return {"observed_at": observed_at, "services": derived}


def build_service_projection(
    provider_services: dict[str, dict[str, Any]],
    verification_services: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    projection: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_SERVICES:
        provider = provider_services[name]
        verification = verification_services[name]
        projection[name] = {
            "provider": provider["provider"],
            "service_instance_id": provider["service_instance_id"],
            "topology_id": provider["topology_id"],
            "fault_domain_count": provider["fault_domain_count"],
            "failover_event_id": provider["failover_event_id"],
            "source_fault_domain": provider["source_fault_domain"],
            "destination_fault_domain": provider["destination_fault_domain"],
            "ha_enabled": True,
            "automatic_failover": True,
            "failover_exercised": True,
            "data_integrity_passed": True,
            "read_probe_passed": True,
            "write_probe_passed": True,
            "started_at": provider["started_at"],
            "recovered_at": provider["recovered_at"],
            "verified_at": verification["verified_at"],
            "provider_event_sha256": provider["provider_event_sha256"],
            "verification_log_sha256": verification["verification_log_sha256"],
        }
    return projection
