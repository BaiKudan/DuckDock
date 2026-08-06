#!/usr/bin/env python3
"""Exercise target Alertmanager delivery and collect signed on-call evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

try:
    from scripts.ga_release_identity import build_release_binding
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_identity import build_release_binding


SCHEMA_VERSION = "duckdock-ga-alerting-evidence-v2"
POLICY_SCHEMA_VERSION = "duckdock-ga-alerting-trust-policy-v1"
DELIVERY_SCHEMA_VERSION = "duckdock-ga-alert-delivery-receipt-v1"
ACK_SCHEMA_VERSION = "duckdock-ga-oncall-acknowledgement-v1"
DELIVERY_SIGNATURE_NAMESPACE = "duckdock-alert-delivery-receipt"
ACK_SIGNATURE_NAMESPACE = "duckdock-oncall-acknowledgement"
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
EXERCISE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")


def _meaningful(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(marker in value for marker in PLACEHOLDER_MARKERS)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _allowed_signer_bindings(path: Path) -> dict[str, set[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read alerting allowed-signers: {exc}") from exc
    bindings: dict[str, set[str]] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        key_index = next(
            (
                index
                for index, field in enumerate(fields[1:], start=1)
                if field.startswith(("ssh-", "ecdsa-", "sk-"))
            ),
            None,
        )
        if key_index is None or key_index + 1 >= len(fields):
            raise ValueError(
                f"alerting allowed-signers line {line_number} has no OpenSSH public key"
            )
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not _meaningful(principal) or any(marker in principal for marker in "*?!"):
                raise ValueError(
                    f"alerting allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("alerting allowed-signers contains no principals")
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    if any(len(owners) != 1 for owners in key_owners.values()):
        raise ValueError("alerting allowed-signers reuses a public key across identities")
    return bindings


def load_alerting_policy(
    path: Path,
    *,
    schedule: str,
    delivery_identity: str,
    oncall_identity: str,
) -> tuple[dict[str, Any], Path, str]:
    policy = _load_object(path, "alerting trust policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "delivery_identities",
        "oncall_schedules",
    }
    delivery_identities = policy.get("delivery_identities")
    schedules = policy.get("oncall_schedules")
    schedules_valid = isinstance(schedules, dict) and bool(schedules) and all(
        _meaningful(name)
        and isinstance(identities, list)
        and bool(identities)
        and all(_meaningful(identity) for identity in identities)
        and len(identities) == len(set(identities))
        for name, identities in schedules.items()
    )
    all_oncall_identities = (
        {identity for identities in schedules.values() for identity in identities}
        if schedules_valid
        else set()
    )
    policy_valid = (
        set(policy) == expected_keys
        and policy.get("schema_version") == POLICY_SCHEMA_VERSION
        and _meaningful(policy.get("policy_id"))
        and _meaningful(policy.get("organization"))
        and isinstance(delivery_identities, list)
        and bool(delivery_identities)
        and all(_meaningful(identity) for identity in delivery_identities)
        and len(delivery_identities) == len(set(delivery_identities))
        and schedules_valid
        and not set(delivery_identities).intersection(all_oncall_identities)
        and schedule in schedules
        and delivery_identity in delivery_identities
        and oncall_identity in schedules.get(schedule, [])
        and delivery_identity != oncall_identity
    )
    if not policy_valid:
        raise ValueError("alerting trust policy does not authorize the requested delivery/on-call identities")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("alerting trust policy has no allowed_signers_path")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_digest = str(policy.get("allowed_signers_sha256", ""))
    if (
        not trust_path.is_file()
        or not DIGEST_RE.fullmatch(expected_digest)
        or _sha256(trust_path) != expected_digest
    ):
        raise ValueError("alerting trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    configured = set(delivery_identities).union(all_oncall_identities)
    if set(bindings) != configured:
        raise ValueError("alerting trust store principals do not exactly match policy identities")
    return policy, trust_path, _sha256(path)


def verify_signature(
    path: Path,
    signature: Path,
    *,
    identity: str,
    allowed_signers: Path,
    namespace: str,
) -> None:
    try:
        completed = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                identity,
                "-n",
                namespace,
                "-s",
                str(signature),
            ],
            input=path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify signed alerting receipt: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid alerting receipt signature: {detail}")


def validate_delivery_receipt(
    receipt: dict[str, Any],
    *,
    exercise_id: str,
    target_environment: str,
    alert_name: str,
    schedule: str,
    event: str,
) -> datetime:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "alert_name",
        "event",
        "receipt_id",
        "deliveries",
        "schedule",
        "delivered",
        "delivered_at",
    }
    delivered_at = _parse_time(receipt.get("delivered_at"))
    deliveries = receipt.get("deliveries")
    delivery_times = (
        [_parse_time(item.get("delivered_at")) for item in deliveries]
        if isinstance(deliveries, list) and all(isinstance(item, dict) for item in deliveries)
        else []
    )
    deliveries_valid = (
        isinstance(deliveries, list)
        and len(deliveries) >= 2
        and all(
            isinstance(item, dict)
            and set(item) == {"channel", "receiver", "provider_receipt_id", "delivered_at"}
            and _meaningful(item.get("channel"))
            and _meaningful(item.get("receiver"))
            and _meaningful(item.get("provider_receipt_id"))
            for item in deliveries
        )
        and all(item is not None for item in delivery_times)
        and len({item["channel"] for item in deliveries}) == len(deliveries)
        and len({item["receiver"] for item in deliveries}) == len(deliveries)
        and len({item["provider_receipt_id"] for item in deliveries}) == len(deliveries)
        and delivered_at is not None
        and max(item for item in delivery_times if item is not None) == delivered_at
    )
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == DELIVERY_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("target_environment") == target_environment
        and receipt.get("alert_name") == alert_name
        and receipt.get("schedule") == schedule
        and receipt.get("event") == event
        and _meaningful(receipt.get("receipt_id"))
        and deliveries_valid
        and receipt.get("delivered") is True
        and delivered_at is not None
    ):
        raise ValueError(f"invalid signed {event} delivery receipt")
    return delivered_at


def validate_acknowledgement(
    receipt: dict[str, Any],
    *,
    exercise_id: str,
    target_environment: str,
    alert_name: str,
    schedule: str,
    oncall_identity: str,
) -> datetime:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "alert_name",
        "receipt_id",
        "schedule",
        "acknowledged",
        "acknowledged_by",
        "acknowledged_at",
    }
    acknowledged_at = _parse_time(receipt.get("acknowledged_at"))
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == ACK_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("target_environment") == target_environment
        and receipt.get("alert_name") == alert_name
        and receipt.get("schedule") == schedule
        and receipt.get("acknowledged_by") == oncall_identity
        and _meaningful(receipt.get("receipt_id"))
        and receipt.get("acknowledged") is True
        and acknowledged_at is not None
    ):
        raise ValueError("invalid signed on-call acknowledgement")
    return acknowledged_at


def wait_signed_receipt(
    path: Path,
    signature: Path,
    *,
    identity: str,
    allowed_signers: Path,
    namespace: str,
    timeout_seconds: float,
    validator: Callable[[dict[str, Any]], datetime],
) -> tuple[dict[str, Any], datetime]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "receipt and signature have not appeared"
    while time.monotonic() <= deadline:
        if path.is_file() and signature.is_file():
            try:
                receipt = _load_object(path, "signed alerting receipt")
                verify_signature(
                    path,
                    signature,
                    identity=identity,
                    allowed_signers=allowed_signers,
                    namespace=namespace,
                )
                observed_at = validator(receipt)
            except ValueError as exc:
                last_error = str(exc)
            else:
                return receipt, observed_at
        time.sleep(min(0.25, max(timeout_seconds, 0.01)))
    raise RuntimeError(f"timed out waiting for signed alerting receipt: {last_error}")


def require_https_url(raw: str) -> str:
    parsed = urllib_parse.urlparse(raw)
    if (
        not _meaningful(raw)
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Alertmanager URL must be a credential-free non-local HTTPS URL")
    return raw.rstrip("/")


class AlertmanagerClient:
    def __init__(
        self,
        base_url: str,
        *,
        ca_file: Path | None,
        bearer_token_file: Path | None,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
        self.bearer_token: str | None = None
        if bearer_token_file is not None:
            token = bearer_token_file.read_text(encoding="utf-8").strip()
            if not token or len(token) > 16_384:
                raise ValueError("Alertmanager bearer token file is empty or unexpectedly large")
            self.bearer_token = token

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        payload: Any | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        body = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.bearer_token is not None:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = urllib_request.Request(
            f"{self.base_url}{endpoint}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib_request.urlopen(
                request,
                context=self.context,
                timeout=self.timeout_seconds,
            ) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                status = response.status
        except (OSError, urllib_error.URLError) as exc:
            raise RuntimeError(f"Alertmanager request failed: {exc}") from exc
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("Alertmanager response exceeds 2 MiB")
        if status < 200 or status >= 300:
            raise RuntimeError(f"Alertmanager returned HTTP {status}")
        try:
            raw_text = raw.decode("utf-8")
            request_text = (body or b"").decode("utf-8")
            decoded = json.loads(raw_text) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Alertmanager returned invalid JSON") from exc
        return decoded, {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "http_status": status,
            "response_bytes": len(raw),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "request_sha256": hashlib.sha256(body or b"").hexdigest(),
            "raw_response_body": raw_text,
            "raw_request_body": request_text,
        }

    def post_alert(
        self,
        *,
        labels: dict[str, str],
        starts_at: datetime,
        ends_at: datetime,
    ) -> dict[str, Any]:
        payload = [
            {
                "labels": labels,
                "annotations": {"summary": "DuckDock GA signed on-call delivery exercise"},
                "startsAt": starts_at.isoformat(),
                "endsAt": ends_at.isoformat(),
            }
        ]
        _, observation = self._request("POST", "/api/v2/alerts", payload=payload)
        observation["request_payload"] = payload
        return observation

    def active_observation(self, alert_name: str) -> dict[str, Any]:
        query = urllib_parse.urlencode(
            {
                "active": "true",
                "silenced": "false",
                "inhibited": "false",
                "unprocessed": "true",
                "filter": f'alertname="{alert_name}"',
            }
        )
        decoded, observation = self._request("GET", f"/api/v2/alerts?{query}")
        if not isinstance(decoded, list):
            raise RuntimeError("Alertmanager alerts API did not return an array")
        matches = [
            item
            for item in decoded
            if isinstance(item, dict)
            and isinstance(item.get("labels"), dict)
            and item["labels"].get("alertname") == alert_name
        ]
        observation["matching_alerts"] = len(matches)
        observation["label_selector_sha256"] = _canonical_digest(
            {"alertname": alert_name}
        )
        return observation


class _TrackingAlertmanagerClient:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.post_count = 0

    def post_alert(
        self,
        *,
        labels: dict[str, str],
        starts_at: datetime,
        ends_at: datetime,
    ) -> dict[str, Any]:
        self.post_count += 1
        observation = self.delegate.post_alert(
            labels=labels,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        return observation

    def active_observation(self, alert_name: str) -> dict[str, Any]:
        return self.delegate.active_observation(alert_name)


def wait_for_alert_state(
    client: AlertmanagerClient,
    alert_name: str,
    *,
    active: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_observation: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        last_observation = client.active_observation(alert_name)
        matches = last_observation.get("matching_alerts")
        if (active and isinstance(matches, int) and matches > 0) or (
            not active and matches == 0
        ):
            last_observation["expected_active"] = active
            last_observation["passed"] = True
            return last_observation
        time.sleep(0.25)
    raise RuntimeError(
        f"Alertmanager did not reach active={active}; last observation={last_observation}"
    )


def _signed_reference(
    path: Path,
    signature: Path,
    *,
    identity: str,
) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "signature_path": str(signature.resolve()),
        "signer_identity": identity,
    }


def _collect_exercise(
    args: argparse.Namespace,
    *,
    client: Any,
) -> dict[str, Any]:
    receipt_paths = (
        args.firing_receipt,
        args.firing_signature,
        args.ack_receipt,
        args.ack_signature,
        args.resolved_receipt,
        args.resolved_signature,
    )
    if any(path.exists() for path in receipt_paths):
        raise ValueError("alerting receipt/signature paths must not exist before the exercise")
    policy, allowed_signers, policy_digest = load_alerting_policy(
        args.alerting_policy,
        schedule=args.oncall_schedule,
        delivery_identity=args.delivery_signer_identity,
        oncall_identity=args.oncall_signer_identity,
    )
    alert_client = client
    alert_name = f"DuckDockGA_{args.exercise_id}"
    labels = {
        "alertname": alert_name,
        "severity": "critical",
        "duckdock_target": args.target_environment,
        "duckdock_exercise": args.exercise_id,
    }
    started_at = datetime.now(timezone.utc)
    firing_expires_at = started_at + timedelta(seconds=args.alert_duration)
    firing_api = alert_client.post_alert(
        labels=labels,
        starts_at=started_at,
        ends_at=firing_expires_at,
    )
    print(
        json.dumps(
            {"phase": "firing", "exercise_id": args.exercise_id, "alert_name": alert_name}
        ),
        flush=True,
    )
    active_observation = wait_for_alert_state(
        alert_client,
        alert_name,
        active=True,
        timeout_seconds=args.alert_state_timeout,
    )
    firing_receipt, firing_at = wait_signed_receipt(
        args.firing_receipt,
        args.firing_signature,
        identity=args.delivery_signer_identity,
        allowed_signers=allowed_signers,
        namespace=DELIVERY_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_delivery_receipt(
            receipt,
            exercise_id=args.exercise_id,
            target_environment=args.target_environment,
            alert_name=alert_name,
            schedule=args.oncall_schedule,
            event="firing",
        ),
    )
    acknowledgement, acknowledged_at = wait_signed_receipt(
        args.ack_receipt,
        args.ack_signature,
        identity=args.oncall_signer_identity,
        allowed_signers=allowed_signers,
        namespace=ACK_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_acknowledgement(
            receipt,
            exercise_id=args.exercise_id,
            target_environment=args.target_environment,
            alert_name=alert_name,
            schedule=args.oncall_schedule,
            oncall_identity=args.oncall_signer_identity,
        ),
    )
    firing_delivery_times = [
        _parse_time(item["delivered_at"]) for item in firing_receipt["deliveries"]
    ]
    if not (
        all(item is not None and started_at <= item <= firing_at for item in firing_delivery_times)
        and started_at <= firing_at <= acknowledged_at <= datetime.now(timezone.utc)
    ):
        raise ValueError("firing and acknowledgement times are not ordered within this exercise")

    resolve_requested_at = datetime.now(timezone.utc)
    resolved_api = alert_client.post_alert(
        labels=labels,
        starts_at=started_at,
        ends_at=resolve_requested_at,
    )
    print(
        json.dumps(
            {"phase": "resolved", "exercise_id": args.exercise_id, "alert_name": alert_name}
        ),
        flush=True,
    )
    inactive_observation = wait_for_alert_state(
        alert_client,
        alert_name,
        active=False,
        timeout_seconds=args.alert_state_timeout,
    )
    resolved_receipt, resolved_at = wait_signed_receipt(
        args.resolved_receipt,
        args.resolved_signature,
        identity=args.delivery_signer_identity,
        allowed_signers=allowed_signers,
        namespace=DELIVERY_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_delivery_receipt(
            receipt,
            exercise_id=args.exercise_id,
            target_environment=args.target_environment,
            alert_name=alert_name,
            schedule=args.oncall_schedule,
            event="resolved",
        ),
    )
    observed_at = datetime.now(timezone.utc)
    receipt_ids = {
        firing_receipt["receipt_id"],
        acknowledgement["receipt_id"],
        resolved_receipt["receipt_id"],
    }
    if len(receipt_ids) != 3 or not (
        acknowledged_at <= resolve_requested_at <= resolved_at <= observed_at
    ):
        raise ValueError("alerting receipt IDs or firing/ack/resolve timeline are invalid")
    firing_targets = {
        (item["channel"], item["receiver"]) for item in firing_receipt["deliveries"]
    }
    resolved_targets = {
        (item["channel"], item["receiver"]) for item in resolved_receipt["deliveries"]
    }
    if firing_targets != resolved_targets:
        raise ValueError("firing and resolved receipts do not cover the same delivery targets")
    resolved_delivery_times = [
        _parse_time(item["delivered_at"]) for item in resolved_receipt["deliveries"]
    ]
    if not all(
        item is not None and resolve_requested_at <= item <= resolved_at
        for item in resolved_delivery_times
    ):
        raise ValueError("resolved delivery targets contain timestamps outside the resolve phase")
    firing_provider_ids = {
        item["provider_receipt_id"] for item in firing_receipt["deliveries"]
    }
    resolved_provider_ids = {
        item["provider_receipt_id"] for item in resolved_receipt["deliveries"]
    }
    if firing_provider_ids.intersection(resolved_provider_ids):
        raise ValueError("provider receipt IDs were reused across firing and resolved delivery")
    if firing_api.get("request_sha256") == resolved_api.get("request_sha256"):
        raise ValueError("firing and resolved Alertmanager requests are not distinct")
    return {
        "schema_version": SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS",
        "passed": True,
        "observed_at": observed_at.isoformat(),
        "test_notification_delivered": True,
        "resolved_notification_delivered": True,
        "oncall_schedule": args.oncall_schedule,
        "alerting_policy": {
            "path": str(args.alerting_policy.resolve()),
            "sha256": policy_digest,
            "policy_id": policy["policy_id"],
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": _sha256(allowed_signers),
        },
        "alert_exercise": {
            "exercise_id": args.exercise_id,
            "alert_name": alert_name,
            "labels": labels,
            "alertmanager_url": args.alertmanager_url,
            "started_at": started_at.isoformat(),
            "firing_expires_at": firing_expires_at.isoformat(),
            "resolve_requested_at": resolve_requested_at.isoformat(),
            "firing_api": firing_api,
            "active_observation": active_observation,
            "resolved_api": resolved_api,
            "inactive_observation": inactive_observation,
        },
        "firing_receipt": {
            **firing_receipt,
            "signed_evidence": _signed_reference(
                args.firing_receipt,
                args.firing_signature,
                identity=args.delivery_signer_identity,
            ),
        },
        "oncall_acknowledgement": {
            **acknowledgement,
            "signed_evidence": _signed_reference(
                args.ack_receipt,
                args.ack_signature,
                identity=args.oncall_signer_identity,
            ),
        },
        "resolved_receipt": {
            **resolved_receipt,
            "signed_evidence": _signed_reference(
                args.resolved_receipt,
                args.resolved_signature,
                identity=args.delivery_signer_identity,
            ),
        },
    }


def collect(
    args: argparse.Namespace,
    *,
    client: AlertmanagerClient | None = None,
) -> dict[str, Any]:
    delegate = client or AlertmanagerClient(
        args.alertmanager_url,
        ca_file=args.ca_file,
        bearer_token_file=args.bearer_token_file,
        timeout_seconds=args.request_timeout,
    )
    tracked = _TrackingAlertmanagerClient(delegate)
    try:
        return _collect_exercise(args, client=tracked)
    except Exception as exercise_error:
        if tracked.post_count > 0:
            cleanup_at = datetime.now(timezone.utc)
            labels = {
                "alertname": f"DuckDockGA_{args.exercise_id}",
                "severity": "critical",
                "duckdock_target": args.target_environment,
                "duckdock_exercise": args.exercise_id,
            }
            try:
                delegate.post_alert(
                    labels=labels,
                    starts_at=cleanup_at,
                    ends_at=cleanup_at,
                )
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"alerting exercise failed ({exercise_error}); cleanup resolve failed ({cleanup_error})"
                ) from exercise_error
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--acknowledge-oncall-exercise", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--alertmanager-url", required=True)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--bearer-token-file", type=Path)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--oncall-schedule", required=True)
    parser.add_argument("--alerting-policy", type=Path, required=True)
    parser.add_argument("--delivery-signer-identity", required=True)
    parser.add_argument("--oncall-signer-identity", required=True)
    parser.add_argument("--firing-receipt", type=Path, required=True)
    parser.add_argument("--firing-signature", type=Path, required=True)
    parser.add_argument("--ack-receipt", type=Path, required=True)
    parser.add_argument("--ack-signature", type=Path, required=True)
    parser.add_argument("--resolved-receipt", type=Path, required=True)
    parser.add_argument("--resolved-signature", type=Path, required=True)
    parser.add_argument("--alert-duration", type=int, default=900)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--alert-state-timeout", type=float, default=60.0)
    parser.add_argument("--receipt-timeout", type=float, default=600.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.acknowledge_oncall_exercise != args.target_environment:
            raise ValueError("--acknowledge-oncall-exercise must exactly equal target environment")
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe, non-placeholder characters")
        if not all(
            _meaningful(value)
            for value in (
                args.target_environment,
                args.oncall_schedule,
                args.delivery_signer_identity,
                args.oncall_signer_identity,
            )
        ):
            raise ValueError("target, schedule and signer identities must be non-placeholder")
        if min(
            args.alert_duration,
            args.request_timeout,
            args.alert_state_timeout,
            args.receipt_timeout,
        ) <= 0:
            raise ValueError("alert and timeout durations must be positive")
        if args.alert_duration < args.receipt_timeout + args.alert_state_timeout + 30:
            raise ValueError(
                "alert duration must cover receipt timeout, state polling and a 30-second margin"
            )
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new path or pass --overwrite-output")
        for path, label in (
            (args.alerting_policy, "alerting policy"),
            (args.ca_file, "CA file"),
            (args.bearer_token_file, "bearer token file"),
        ):
            if path is not None and not path.is_file():
                raise ValueError(f"{label} does not exist")
        args.alertmanager_url = require_https_url(args.alertmanager_url)
        args.release_binding = build_release_binding(
            scope="target-production",
            target_environment=args.target_environment,
            source_commit=args.source_commit,
            backend_image=args.backend_image,
            frontend_image=args.frontend_image,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = collect(args)
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Target alerting collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
