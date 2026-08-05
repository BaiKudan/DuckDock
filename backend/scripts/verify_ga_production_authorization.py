#!/usr/bin/env python3
"""Validate a DuckDock 2.0 target-environment GA authorization bundle.

The in-product GA readiness endpoint proves application-level integrity. This
gate deliberately adds the deployment, independent-security, capacity,
recovery, HA, on-call and four-role authorization evidence needed to call a
specific immutable release production GA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse


SCHEMA_VERSION = "duckdock-ga-production-authorization-v1"
REQUIRED_CONTROLS = {
    "application_readiness",
    "tls",
    "secrets",
    "network",
    "alerting",
    "recovery",
    "capacity",
    "high_availability",
    "security_assessment",
}
REQUIRED_APPROVAL_ROLES = {"Product", "Architecture", "Security", "Operations"}
EXTERNAL_CHECK_KEYS = {
    "security_assessment",
    "security_assessment_signature",
    "approval_roles",
    "approval_four_eyes",
    "approval_Product",
    "approval_Architecture",
    "approval_Security",
    "approval_Operations",
}
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Check:
    key: str
    owner: str
    passed: bool
    observed: str
    expected: str
    detail: str


class Gate:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(
        self,
        key: str,
        *,
        owner: str,
        passed: bool,
        observed: Any,
        expected: str,
        detail: str,
    ) -> None:
        self.checks.append(
            Check(
                key=key,
                owner=owner,
                passed=bool(passed),
                observed=str(observed),
                expected=expected,
                detail=detail,
            )
        )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read authorization JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("authorization root must be an object")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def lint_authorization(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("release", "target", "controls"):
        if not isinstance(document.get(key), dict):
            errors.append(f"{key} must be an object")
    if not isinstance(document.get("approvals"), list):
        errors.append("approvals must be an array")
    controls = document.get("controls")
    if isinstance(controls, dict):
        missing = REQUIRED_CONTROLS - set(controls)
        extra = set(controls) - REQUIRED_CONTROLS
        if missing:
            errors.append(f"controls missing: {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"unknown controls: {', '.join(sorted(extra))}")
        for name, control in controls.items():
            if not isinstance(control, dict):
                errors.append(f"controls.{name} must be an object")
                continue
            if not isinstance(control.get("evidence"), dict):
                errors.append(f"controls.{name}.evidence must be an object")
    return errors


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _resolve_file(raw: Any, authorization_path: Path) -> Path | None:
    if not isinstance(raw, str) or not raw or any(marker in raw for marker in PLACEHOLDER_MARKERS):
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    adjacent = (authorization_path.parent / path).resolve()
    if adjacent.exists():
        return adjacent
    return (Path.cwd() / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _evidence_json(
    control: dict[str, Any],
    authorization_path: Path,
) -> dict[str, Any] | None:
    evidence = control.get("evidence")
    if not isinstance(evidence, dict):
        return None
    path = _resolve_file(evidence.get("path"), authorization_path)
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _origin(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _meaningful_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(marker in value for marker in PLACEHOLDER_MARKERS)


def _control_observed_at(control: dict[str, Any]) -> datetime | None:
    evidence = control.get("evidence")
    return _parse_time(evidence.get("observed_at")) if isinstance(evidence, dict) else None


def _report_release_target_binding(
    report: dict[str, Any] | None,
    *,
    schema_version: str,
    status: str,
    control: dict[str, Any],
    target: dict[str, Any],
    release: dict[str, Any],
) -> bool:
    if not isinstance(report, dict):
        return False
    images = report.get("images")
    observed_at = _control_observed_at(control)
    return (
        report.get("schema_version") == schema_version
        and report.get("scope") == "target-production"
        and report.get("status") == status
        and report.get("passed") is True
        and report.get("target_environment") == target.get("target_id")
        and report.get("source_commit") == release.get("git_commit")
        and observed_at is not None
        and _parse_time(report.get("observed_at")) == observed_at
        and isinstance(images, dict)
        and isinstance(images.get("backend"), dict)
        and isinstance(images.get("frontend"), dict)
        and images["backend"].get("name") == release.get("backend_image")
        and images["frontend"].get("name") == release.get("frontend_image")
    )


def _contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "credential",
        "credential_value",
        "password",
        "password_value",
        "plaintext",
        "plaintext_value",
        "private_key",
        "raw_secret",
        "secret_value",
        "secret_values",
        "token",
        "token_value",
    }
    if isinstance(value, dict):
        return any(str(key).lower() in forbidden or _contains_secret_material_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret_material_key(item) for item in value)
    return False


def _ordered_report_times(
    *values: Any,
    no_later_than: datetime | None = None,
) -> bool:
    parsed = [_parse_time(value) for value in values]
    if any(value is None for value in parsed):
        return False
    concrete = [value for value in parsed if value is not None]
    return concrete == sorted(concrete) and (no_later_than is None or concrete[-1] <= no_later_than)


def _ha_component(report: dict[str, Any], snapshot: str, component: str) -> dict[str, Any]:
    snapshots = report.get(snapshot)
    if not isinstance(snapshots, dict):
        return {}
    value = snapshots.get(component)
    return value if isinstance(value, dict) else {}


def _ha_ready_replicas(report: dict[str, Any], snapshot: str, component: str) -> int:
    try:
        return int(_ha_component(report, snapshot, component).get("ready_replicas", -1))
    except (TypeError, ValueError):
        return -1


def _ha_ready_zones(report: dict[str, Any], snapshot: str, component: str) -> set[str]:
    pods = _ha_component(report, snapshot, component).get("pods")
    if not isinstance(pods, list):
        return set()
    return {
        str(item.get("zone"))
        for item in pods
        if isinstance(item, dict) and item.get("ready") is True and item.get("zone")
    }


def _ha_ready_nodes(report: dict[str, Any], snapshot: str, component: str) -> set[str]:
    pods = _ha_component(report, snapshot, component).get("pods")
    if not isinstance(pods, list):
        return set()
    return {
        str(item.get("node"))
        for item in pods
        if isinstance(item, dict) and item.get("ready") is True and item.get("node")
    }


def _ha_snapshot_consistent(report: dict[str, Any], snapshot: str, component: str) -> bool:
    value = _ha_component(report, snapshot, component)
    pods = value.get("pods")
    if not isinstance(pods, list):
        return False
    ready_pods = [item for item in pods if isinstance(item, dict) and item.get("ready") is True]
    identities = [str(item.get("pod", "")) for item in ready_pods]
    return (
        _ha_ready_replicas(report, snapshot, component) == len(ready_pods)
        and bool(identities)
        and all(identities)
        and len(set(identities)) == len(identities)
        and all(item.get("node") and item.get("zone") for item in ready_pods)
    )


def _evidence_check(
    gate: Gate,
    *,
    key: str,
    owner: str,
    control: dict[str, Any],
    authorization_path: Path,
    now: datetime,
    maximum_age: timedelta,
) -> tuple[bool, datetime | None]:
    evidence = control.get("evidence")
    if not isinstance(evidence, dict):
        gate.add(
            f"{key}_evidence",
            owner=owner,
            passed=False,
            observed="missing",
            expected="existing, digest-bound evidence file",
            detail="Every production assertion must reference retained evidence.",
        )
        return False, None
    path = _resolve_file(evidence.get("path"), authorization_path)
    expected_digest = str(evidence.get("sha256", ""))
    observed_at = _parse_time(evidence.get("observed_at"))
    exists = path is not None and path.is_file()
    actual_digest = _sha256(path) if exists else "missing"
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    fresh = observed_at is not None and timedelta(0) <= now - observed_at <= maximum_age
    passed = exists and digest_ok and fresh
    gate.add(
        f"{key}_evidence",
        owner=owner,
        passed=passed,
        observed=f"path={path or 'missing'}, digest={actual_digest}, observed_at={observed_at}",
        expected=f"file digest match; age <= {maximum_age.days}d",
        detail="Evidence is content-addressed and freshness bounded; JSON claims alone are insufficient.",
    )
    return passed, observed_at


def _https_url(value: Any) -> bool:
    if not isinstance(value, str) or any(marker in value for marker in PLACEHOLDER_MARKERS):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname not in {"localhost", "127.0.0.1"}


def _release_digest(release: dict[str, Any], target: dict[str, Any], controls: dict[str, Any]) -> str:
    evidence_digests = {
        key: value.get("evidence", {}).get("sha256") if isinstance(value, dict) else None
        for key, value in sorted(controls.items())
    }
    canonical = json.dumps(
        {"schema_version": SCHEMA_VERSION, "release": release, "target": target, "evidence_digests": evidence_digests},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_ssh_payload(
    *,
    identity: str,
    allowed_signers: Path,
    signature: Path,
    namespace: str,
    payload: bytes,
) -> tuple[bool, str]:
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
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    return completed.returncode == 0, completed.stdout.decode("utf-8", errors="replace").strip()


def _verify_ssh_signature(
    *,
    identity: str,
    allowed_signers: Path,
    signature: Path,
    release_digest: str,
) -> tuple[bool, str]:
    statement = f"{SCHEMA_VERSION}:{release_digest}\n".encode()
    return _verify_ssh_payload(
        identity=identity,
        allowed_signers=allowed_signers,
        signature=signature,
        namespace="duckdock-ga",
        payload=statement,
    )


def _verify_signed_evidence_file(
    signed_file: dict[str, Any],
    *,
    authorization_path: Path,
    namespace: str,
) -> tuple[bool, str]:
    path = _resolve_file(signed_file.get("path"), authorization_path)
    allowed_signers = _resolve_file(signed_file.get("allowed_signers_path"), authorization_path)
    signature = _resolve_file(signed_file.get("signature_path"), authorization_path)
    identity = signed_file.get("signer_identity")
    expected_digest = str(signed_file.get("sha256", ""))
    exists = path is not None and path.is_file()
    actual_digest = _sha256(path) if exists else "missing"
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    artifacts_ok = (
        _meaningful_string(identity)
        and allowed_signers is not None
        and allowed_signers.is_file()
        and signature is not None
        and signature.is_file()
    )
    signature_ok = False
    signature_detail = "missing signer identity or signature artifacts"
    if exists and artifacts_ok:
        signature_ok, signature_detail = _verify_ssh_payload(
            identity=identity,
            allowed_signers=allowed_signers,
            signature=signature,
            namespace=namespace,
            payload=path.read_bytes(),
        )
    return (
        exists and digest_ok and artifacts_ok and signature_ok,
        f"path={path or 'missing'}, digest={actual_digest}, signer={identity or 'missing'}, signature={signature_detail}",
    )


def evaluate(
    document: dict[str, Any],
    *,
    authorization_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    lint_errors = lint_authorization(document)
    if lint_errors:
        raise ValueError("; ".join(lint_errors))
    current = now or datetime.now(timezone.utc)
    gate = Gate()
    release = _object(document["release"], "release")
    target = _object(document["target"], "target")
    controls = _object(document["controls"], "controls")

    version = str(release.get("version", ""))
    gate.add(
        "release_version",
        owner="Product",
        passed=version == "2.0.0",
        observed=version or "missing",
        expected="2.0.0",
        detail="RC identifiers cannot be used for final GA authorization.",
    )
    commit = str(release.get("git_commit", ""))
    gate.add(
        "release_commit",
        owner="Architecture",
        passed=bool(COMMIT_RE.fullmatch(commit)),
        observed=commit or "missing",
        expected="immutable 40-character Git commit",
        detail="All evidence and approvals bind to one source revision.",
    )
    for image_key in ("backend_image", "frontend_image"):
        image = str(release.get(image_key, ""))
        gate.add(
            image_key,
            owner="Security",
            passed=bool(IMAGE_RE.fullmatch(image)),
            observed=image or "missing",
            expected="registry/repository@sha256:<64 hex>",
            detail="Tags are mutable and cannot identify a GA production artifact.",
        )
    contract_digest = str(release.get("contract_digest", ""))
    gate.add(
        "contract_digest",
        owner="Architecture",
        passed=bool(DIGEST_RE.fullmatch(contract_digest)),
        observed=contract_digest or "missing",
        expected="64-character SHA-256",
        detail="The authorization binds the frozen v2 API contract.",
    )

    fault_domains = target.get("fault_domains")
    target_mode = target.get("deployment_mode")
    target_ok = (
        target.get("environment") == "production"
        and target_mode == "kubernetes-ha"
        and isinstance(target.get("target_id"), str)
        and not any(marker in str(target.get("target_id")) for marker in PLACEHOLDER_MARKERS)
        and isinstance(fault_domains, list)
        and len({str(item) for item in fault_domains if str(item).strip()}) >= 2
        and _https_url(target.get("public_base_url"))
        and _https_url(target.get("object_store_url"))
    )
    gate.add(
        "target_identity",
        owner="Architecture",
        passed=target_ok,
        observed=f"target_id={target.get('target_id')}, environment={target.get('environment')}, mode={target_mode}, fault_domains={fault_domains}",
        expected="named production kubernetes-ha target; >=2 fault domains; HTTPS app/object URLs",
        detail="Authorization is target-specific and cannot be copied from local Compose evidence.",
    )

    evidence_times: list[datetime] = []
    evidence_owners = {
        "application_readiness": "Architecture",
        "tls": "Security",
        "secrets": "Security",
        "network": "Security",
        "alerting": "Operations",
        "recovery": "Operations",
        "capacity": "Architecture",
        "high_availability": "Architecture",
        "security_assessment": "Security",
    }
    evidence_ages = {
        "application_readiness": timedelta(days=7),
        "tls": timedelta(days=7),
        "secrets": timedelta(days=30),
        "network": timedelta(days=30),
        "alerting": timedelta(days=30),
        "recovery": timedelta(days=30),
        "capacity": timedelta(days=30),
        "high_availability": timedelta(days=30),
        "security_assessment": timedelta(days=90),
    }
    for key in sorted(REQUIRED_CONTROLS):
        _, observed_at = _evidence_check(
            gate,
            key=key,
            owner=evidence_owners[key],
            control=_object(controls[key], f"controls.{key}"),
            authorization_path=authorization_path,
            now=current,
            maximum_age=evidence_ages[key],
        )
        if observed_at is not None:
            evidence_times.append(observed_at)

    app = controls["application_readiness"]
    app_report = _evidence_json(app, authorization_path)
    app_report_matches = (
        app_report is not None
        and app_report.get("status") == app.get("status")
        and int(app_report.get("pass_count", -1)) == int(app.get("pass_count", -2))
        and int(app_report.get("block_count", -1)) == int(app.get("block_count", -2))
        and app_report.get("contract_version") == app.get("contract_version")
        and app_report.get("contract_digest") == app.get("contract_digest")
        and app_report.get("current_db_revision") == app.get("database_revision")
        and app_report.get("expected_db_revision") == app.get("expected_database_revision")
        and isinstance(app_report.get("checks"), list)
        and all(isinstance(item, dict) and item.get("status") == "PASS" for item in app_report["checks"])
    )
    gate.add(
        "application_readiness",
        owner="Architecture",
        passed=(
            app.get("status") == "READY"
            and app.get("block_count") == 0
            and int(app.get("pass_count", 0)) >= 14
            and app.get("contract_version") == version
            and app.get("contract_digest") == contract_digest
            and app.get("database_revision") == app.get("expected_database_revision")
            and app_report_matches
        ),
        observed=(
            f"status={app.get('status')}, pass={app.get('pass_count')}, block={app.get('block_count')}, "
            f"contract={app.get('contract_version')}@{app.get('contract_digest')}, "
            f"db={app.get('database_revision')}"
        ),
        expected="READY; >=14 pass; 0 block; final release contract digest/version; DB at expected revision",
        detail="The content-addressed raw readiness response must match every imported claim; it does not replace target authorization.",
    )

    tls = controls["tls"]
    negotiated = tls.get("negotiated_protocols")
    tls_report = _evidence_json(tls, authorization_path)
    tls_report_matches_target = (
        tls_report is not None
        and tls_report.get("schema_version") == "duckdock-ga-tls-probe-v1"
        and tls_report.get("status") == "PASS"
        and _origin(tls_report.get("application_url")) == str(target.get("public_base_url", "")).rstrip("/")
        and _origin(tls_report.get("object_store_url")) == str(target.get("object_store_url", "")).rstrip("/")
        and tls_report.get("negotiated_protocols") == negotiated
        and tls_report.get("legacy_protocols_rejected") == tls.get("legacy_protocols_rejected")
        and int(tls_report.get("certificate_days_remaining", -1)) == int(tls.get("certificate_days_remaining", -2))
        and tls_report.get("hostname_verified") is tls.get("hostname_verified")
        and int(tls_report.get("hsts_max_age_seconds", -1)) == int(tls.get("hsts_max_age_seconds", -2))
        and isinstance(tls_report.get("endpoints"), dict)
        and set(tls_report["endpoints"]) == {"application", "object_store"}
        and all(
            isinstance(item, dict) and item.get("passed") is True
            for item in (tls_report.get("endpoints") or {}).values()
        )
    )
    tls_ok = (
        tls.get("status") == "PASS"
        and isinstance(negotiated, list)
        and set(negotiated).issubset({"TLSv1.2", "TLSv1.3"})
        and "TLSv1.2" in negotiated
        and tls.get("legacy_protocols_rejected") == ["TLSv1", "TLSv1.1"]
        and int(tls.get("certificate_days_remaining", -1)) >= 30
        and int(tls.get("hsts_max_age_seconds", 0)) >= 31_536_000
        and tls.get("hostname_verified") is True
        and tls_report_matches_target
    )
    gate.add(
        "tls",
        owner="Security",
        passed=tls_ok,
        observed=f"status={tls.get('status')}, protocols={negotiated}, cert_days={tls.get('certificate_days_remaining')}",
        expected="TLS 1.2/1.3 only; hostname valid; cert >=30d; HSTS >=1y",
        detail="The content-addressed probe JSON must match both actual public target origins and every claimed TLS metric.",
    )

    secrets = controls["secrets"]
    secrets_report = _evidence_json(secrets, authorization_path)
    secret_store = (
        secrets_report.get("secret_store")
        if isinstance(secrets_report, dict) and isinstance(secrets_report.get("secret_store"), dict)
        else {}
    )
    secret_rotation = (
        secrets_report.get("rotation")
        if isinstance(secrets_report, dict) and isinstance(secrets_report.get("rotation"), dict)
        else {}
    )
    secret_classes = secret_rotation.get("secret_classes")
    secrets_report_matches = (
        _report_release_target_binding(
            secrets_report,
            schema_version="duckdock-ga-secrets-evidence-v1",
            status="PASS",
            control=secrets,
            target=target,
            release=release,
        )
        and secrets_report.get("provider") == secrets.get("provider")
        and secrets_report.get("plaintext_env_persisted") is secrets.get("plaintext_env_persisted")
        and secrets_report.get("rotation_tested") is secrets.get("rotation_tested")
        and secret_store.get("encrypted_at_rest") is True
        and secret_store.get("access_audit_enabled") is True
        and secret_store.get("credentials_external_to_evidence") is True
        and secret_rotation.get("executed") is True
        and secret_rotation.get("old_credentials_rejected") is True
        and secret_rotation.get("workloads_reloaded") is True
        and secret_rotation.get("audit_event_recorded") is True
        and not _contains_secret_material_key(secrets_report)
        and isinstance(secret_classes, list)
        and bool(secret_classes)
        and all(_meaningful_string(item) for item in secret_classes)
        and _ordered_report_times(
            secret_rotation.get("started_at"),
            secret_rotation.get("completed_at"),
            no_later_than=_control_observed_at(secrets),
        )
    )
    secrets_ok = (
        secrets.get("status") == "PASS"
        and secrets.get("plaintext_env_persisted") is False
        and secrets.get("rotation_tested") is True
        and secrets.get("provider") in {"SOPS/age", "External Secrets", "Vault", "cloud-secret-manager"}
        and secrets_report_matches
    )
    gate.add(
        "secrets",
        owner="Security",
        passed=secrets_ok,
        observed=f"status={secrets.get('status')}, provider={secrets.get('provider')}, rotation={secrets.get('rotation_tested')}",
        expected="approved secret manager; no persisted plaintext env; rotation tested",
        detail="The target report must bind the release and prove audited encrypted storage, credential rejection and workload reload without retaining secret values.",
    )

    network = controls["network"]
    network_report = _evidence_json(network, authorization_path)
    external_scan = (
        network_report.get("external_scan")
        if isinstance(network_report, dict) and isinstance(network_report.get("external_scan"), dict)
        else {}
    )
    policy_tests = (
        network_report.get("policy_tests")
        if isinstance(network_report, dict) and isinstance(network_report.get("policy_tests"), dict)
        else {}
    )
    network_claims_match = isinstance(network_report, dict) and all(
        network_report.get(key) == network.get(key)
        for key in (
            "public_tcp_ports",
            "database_public",
            "redis_public",
            "object_store_direct_public",
            "default_deny_ingress",
            "egress_allowlist_enforced",
        )
    )
    network_report_matches = (
        _report_release_target_binding(
            network_report,
            schema_version="duckdock-ga-network-evidence-v1",
            status="PASS",
            control=network,
            target=target,
            release=release,
        )
        and network_claims_match
        and _meaningful_string(network_report.get("enforced_by"))
        and external_scan.get("transport") == "network TCP scan from outside target"
        and external_scan.get("discovered_tcp_ports") == [443]
        and external_scan.get("passed") is True
        and policy_tests.get("default_deny_ingress_exercised") is True
        and policy_tests.get("unapproved_egress_denied") is True
        and policy_tests.get("approved_egress_allowed") is True
        and policy_tests.get("private_data_services_unreachable_externally") is True
        and policy_tests.get("passed") is True
    )
    network_ok = (
        network.get("status") == "PASS"
        and network.get("public_tcp_ports") == [443]
        and network.get("database_public") is False
        and network.get("redis_public") is False
        and network.get("object_store_direct_public") is False
        and network.get("default_deny_ingress") is True
        and network.get("egress_allowlist_enforced") is True
        and network_report_matches
    )
    gate.add(
        "network",
        owner="Security",
        passed=network_ok,
        observed=f"status={network.get('status')}, public_ports={network.get('public_tcp_ports')}, egress={network.get('egress_allowlist_enforced')}",
        expected="only 443 public; data stores private; default deny; target egress allowlist",
        detail="The release-bound target report must include an outside TCP scan plus exercised ingress and egress policy paths; YAML declarations alone do not pass.",
    )

    alerting = controls["alerting"]
    alerting_report = _evidence_json(alerting, authorization_path)
    firing_receipt = (
        alerting_report.get("firing_receipt")
        if isinstance(alerting_report, dict) and isinstance(alerting_report.get("firing_receipt"), dict)
        else {}
    )
    resolved_receipt = (
        alerting_report.get("resolved_receipt")
        if isinstance(alerting_report, dict) and isinstance(alerting_report.get("resolved_receipt"), dict)
        else {}
    )
    acknowledgement = (
        alerting_report.get("oncall_acknowledgement")
        if isinstance(alerting_report, dict) and isinstance(alerting_report.get("oncall_acknowledgement"), dict)
        else {}
    )
    alerting_report_matches = (
        _report_release_target_binding(
            alerting_report,
            schema_version="duckdock-ga-alerting-evidence-v1",
            status="PASS",
            control=alerting,
            target=target,
            release=release,
        )
        and alerting_report.get("test_notification_delivered") is alerting.get("test_notification_delivered")
        and alerting_report.get("resolved_notification_delivered") is alerting.get("resolved_notification_delivered")
        and alerting_report.get("oncall_schedule") == alerting.get("oncall_schedule")
        and firing_receipt.get("delivered") is True
        and resolved_receipt.get("delivered") is True
        and _meaningful_string(firing_receipt.get("receipt_id"))
        and _meaningful_string(resolved_receipt.get("receipt_id"))
        and firing_receipt.get("receipt_id") != resolved_receipt.get("receipt_id")
        and acknowledgement.get("acknowledged") is True
        and acknowledgement.get("schedule") == alerting.get("oncall_schedule")
        and _meaningful_string(acknowledgement.get("receipt_id"))
        and _ordered_report_times(
            firing_receipt.get("delivered_at"),
            acknowledgement.get("acknowledged_at"),
            resolved_receipt.get("delivered_at"),
            no_later_than=_control_observed_at(alerting),
        )
    )
    alerting_ok = (
        alerting.get("status") == "PASS"
        and alerting.get("test_notification_delivered") is True
        and alerting.get("resolved_notification_delivered") is True
        and isinstance(alerting.get("oncall_schedule"), str)
        and not any(marker in str(alerting.get("oncall_schedule")) for marker in PLACEHOLDER_MARKERS)
        and alerting_report_matches
    )
    gate.add(
        "alerting",
        owner="Operations",
        passed=alerting_ok,
        observed=f"status={alerting.get('status')}, fired={alerting.get('test_notification_delivered')}, resolved={alerting.get('resolved_notification_delivered')}",
        expected="firing + resolved notifications delivered to a named on-call schedule",
        detail="The release-bound target report must retain distinct firing/resolved receipts and a timestamped acknowledgement from the named on-call schedule.",
    )

    recovery = controls["recovery"]
    target_rpo = int(target.get("maximum_rpo_seconds", 900))
    target_rto = int(target.get("maximum_rto_seconds", 14_400))
    recovery_report = _evidence_json(recovery, authorization_path)
    backup_report = (
        recovery_report.get("backup")
        if isinstance(recovery_report, dict) and isinstance(recovery_report.get("backup"), dict)
        else {}
    )
    restore_report = (
        recovery_report.get("restore")
        if isinstance(recovery_report, dict) and isinstance(recovery_report.get("restore"), dict)
        else {}
    )
    verification_report = (
        recovery_report.get("verification")
        if isinstance(recovery_report, dict) and isinstance(recovery_report.get("verification"), dict)
        else {}
    )
    recovery_claims_match = isinstance(recovery_report, dict) and all(
        recovery_report.get(key) == recovery.get(key)
        for key in (
            "offsite_media",
            "encrypted",
            "immutable_or_object_locked",
            "rpo_seconds",
            "rto_seconds",
            "mysql_rows_verified",
            "objects_verified",
            "git_repositories_verified",
        )
    )
    recovery_report_matches = (
        _report_release_target_binding(
            recovery_report,
            schema_version="duckdock-ga-recovery-evidence-v1",
            status="PASSED",
            control=recovery,
            target=target,
            release=release,
        )
        and recovery_claims_match
        and backup_report.get("manifest_schema_version") == "duckdock-secure-backup-v1"
        and bool(DIGEST_RE.fullmatch(str(backup_report.get("manifest_sha256", ""))))
        and backup_report.get("signature_verified") is True
        and backup_report.get("decryption_key_external") is True
        and restore_report.get("destructive_restore") is True
        and _meaningful_string(restore_report.get("target_environment"))
        and restore_report.get("target_environment") != target.get("target_id")
        and restore_report.get("production_data_overwrite") is False
        and restore_report.get("integrity_digest_verified") is True
        and _ordered_report_times(
            restore_report.get("started_at"),
            restore_report.get("completed_at"),
            no_later_than=_control_observed_at(recovery),
        )
        and verification_report.get("mysql_rows_verified") == recovery.get("mysql_rows_verified")
        and verification_report.get("objects_verified") == recovery.get("objects_verified")
        and verification_report.get("git_repositories_verified") is recovery.get("git_repositories_verified")
        and verification_report.get("passed") is True
    )
    recovery_ok = (
        recovery.get("status") == "PASSED"
        and recovery.get("offsite_media") is True
        and recovery.get("encrypted") is True
        and recovery.get("immutable_or_object_locked") is True
        and int(recovery.get("rpo_seconds", target_rpo + 1)) <= target_rpo
        and int(recovery.get("rto_seconds", target_rto + 1)) <= target_rto
        and int(recovery.get("mysql_rows_verified", 0)) > 0
        and int(recovery.get("objects_verified", 0)) > 0
        and recovery.get("git_repositories_verified") is True
        and recovery_report_matches
    )
    gate.add(
        "recovery",
        owner="Operations",
        passed=recovery_ok,
        observed=f"status={recovery.get('status')}, RPO={recovery.get('rpo_seconds')}, RTO={recovery.get('rto_seconds')}",
        expected=f"offsite encrypted immutable restore; RPO<={target_rpo}s; RTO<={target_rto}s; DB/object/Git verified",
        detail="The release-bound report must prove a signed offsite encrypted immutable backup and a destructive non-production restore with DB/object/Git integrity checks.",
    )

    capacity = controls["capacity"]
    minimum_sustained_rps = float(target.get("minimum_sustained_rps", 50))
    capacity_report = _evidence_json(capacity, authorization_path)
    sustained_report = None
    timeline_report: dict[str, Any] = {}
    if capacity_report is not None and isinstance(capacity_report.get("phases"), list):
        sustained_report = next(
            (
                phase
                for phase in capacity_report["phases"]
                if isinstance(phase, dict) and phase.get("name") == "sustained"
            ),
            None,
        )
    if capacity_report is not None and isinstance(capacity_report.get("post_growth_timeline_query"), dict):
        timeline_report = capacity_report["post_growth_timeline_query"]
    report_matches_target = (
        capacity_report is not None
        and capacity_report.get("schema_version") == "duckdock-target-capacity-gate-v1"
        and capacity_report.get("passed") is True
        and capacity_report.get("transport") == "network HTTPS against target"
        and capacity_report.get("target_environment") == target.get("target_id")
        and str(capacity_report.get("base_url", "")).rstrip("/") == str(target.get("public_base_url", "")).rstrip("/")
        and isinstance(sustained_report, dict)
        and sustained_report.get("passed") is True
        and int(sustained_report.get("duration_seconds", 0)) == int(capacity.get("sustained_seconds", -1))
        and float(sustained_report.get("target_rate", 0)) == float(capacity.get("sustained_rps", -1))
        and int(capacity_report.get("materialized_runs", -1)) == int(capacity.get("materialized_runs", -2))
        and float(capacity_report.get("error_rate", -1)) == float(capacity.get("error_rate", -2))
        and float(sustained_report.get("p95_ms", -1)) == float(capacity.get("write_p95_ms", -2))
        and float(timeline_report.get("p95_ms", -1)) == float(capacity.get("timeline_p95_ms", -2))
        and timeline_report.get("passed") is capacity.get("post_growth_query_passed")
    )
    capacity_ok = (
        capacity.get("status") == "PASSED"
        and int(capacity.get("sustained_seconds", 0)) >= 900
        and float(capacity.get("sustained_rps", 0)) >= minimum_sustained_rps
        and int(capacity.get("materialized_runs", 0)) >= 50_000
        and float(capacity.get("error_rate", 1)) <= 0.001
        and float(capacity.get("write_p95_ms", 1e12)) <= 1_000
        and float(capacity.get("timeline_p95_ms", 1e12)) <= 250
        and capacity.get("post_growth_query_passed") is True
        and report_matches_target
    )
    gate.add(
        "capacity",
        owner="Architecture",
        passed=capacity_ok,
        observed=(
            f"status={capacity.get('status')}, duration={capacity.get('sustained_seconds')}s, "
            f"rps={capacity.get('sustained_rps')}, runs={capacity.get('materialized_runs')}, "
            f"errors={capacity.get('error_rate')}"
        ),
        expected=f">=900s at >={minimum_sustained_rps} rps; >=50k rows; error<=0.1%; write p95<=1s; query p95<=250ms",
        detail="The evidence must be a passing network-HTTPS target report; local ASGI/MySQL engineering results cannot authorize production.",
    )

    ha = controls["high_availability"]
    replica_counts = ha.get("replica_counts")
    ha_report = _evidence_json(ha, authorization_path)
    report_counts = ha_report.get("replica_counts") if isinstance(ha_report, dict) else None
    report_domains_raw = ha_report.get("fault_domains") if isinstance(ha_report, dict) else None
    report_domains = (
        {str(item) for item in report_domains_raw if str(item).strip()}
        if isinstance(report_domains_raw, list)
        else set()
    )
    target_domains = (
        {str(item) for item in fault_domains if str(item).strip()} if isinstance(fault_domains, list) else set()
    )
    report_fault = ha_report.get("fault_injection") if isinstance(ha_report, dict) else None
    report_probe = ha_report.get("availability_probe") if isinstance(ha_report, dict) else None
    report_network = ha_report.get("network_policy") if isinstance(ha_report, dict) else None
    report_state = ha_report.get("state_services") if isinstance(ha_report, dict) else None
    evidence_observed_at = _parse_time(
        (ha.get("evidence") or {}).get("observed_at") if isinstance(ha.get("evidence"), dict) else None
    )
    expected_counts = (
        {name: int(replica_counts.get(name, 0)) for name in ("backend", "frontend", "worker", "beat")}
        if isinstance(replica_counts, dict)
        else {}
    )
    report_counts_match = isinstance(report_counts, dict) and all(
        int(report_counts.get(name, -1)) == expected_counts.get(name)
        for name in ("backend", "frontend", "worker", "beat")
    )
    before_ready = all(
        _ha_snapshot_consistent(ha_report or {}, "before", name)
        and _ha_ready_replicas(ha_report or {}, "before", name) >= expected_counts.get(name, 1)
        for name in ("backend", "frontend", "worker", "beat")
    )
    drain_ready = all(
        _ha_snapshot_consistent(ha_report or {}, "after_zone_drain", name)
        and _ha_ready_replicas(ha_report or {}, "after_zone_drain", name) >= expected_counts.get(name, 1)
        for name in ("backend", "frontend", "worker", "beat")
    )
    restored_ready_and_spread = all(
        _ha_snapshot_consistent(ha_report or {}, "after_zone_return_and_rolling_rebalance", name)
        and _ha_ready_replicas(ha_report or {}, "after_zone_return_and_rolling_rebalance", name)
        >= expected_counts.get(name, 1)
        and target_domains.issubset(_ha_ready_zones(ha_report or {}, "after_zone_return_and_rolling_rebalance", name))
        for name in ("backend", "frontend", "worker")
    )
    before_spread = all(
        target_domains.issubset(_ha_ready_zones(ha_report or {}, "before", name))
        for name in ("backend", "frontend", "worker")
    )
    drained_zone_absent = isinstance(report_fault, dict) and all(
        report_fault.get("drained_zone") not in _ha_ready_zones(ha_report or {}, "after_zone_drain", name)
        and report_fault.get("drained_node") not in _ha_ready_nodes(ha_report or {}, "after_zone_drain", name)
        for name in ("backend", "frontend", "worker", "beat")
    )
    report_matches_target = (
        isinstance(ha_report, dict)
        and ha_report.get("schema_version") == "duckdock-kubernetes-ha-failover-v1"
        and ha_report.get("scope") == "target-production"
        and ha_report.get("status") == "PASS"
        and ha_report.get("passed") is True
        and ha_report.get("target_environment") == target.get("target_id")
        and ha_report.get("source_commit") == commit
        and _parse_time(ha_report.get("observed_at")) == evidence_observed_at
        and isinstance(ha_report.get("images"), dict)
        and isinstance(ha_report["images"].get("backend"), dict)
        and isinstance(ha_report["images"].get("frontend"), dict)
        and ha_report["images"]["backend"].get("name") == release.get("backend_image")
        and ha_report["images"]["frontend"].get("name") == release.get("frontend_image")
        and target_domains == report_domains
        and len(report_domains) >= 2
        and int(ha_report.get("fault_domains_exercised", 0)) == len(report_domains)
        and int(ha_report.get("fault_domains_exercised", 0)) == int(ha.get("fault_domains_exercised", -1))
        and report_counts_match
        and before_ready
        and before_spread
        and drain_ready
        and drained_zone_absent
        and restored_ready_and_spread
        and isinstance(report_fault, dict)
        and report_fault.get("drained_zone") in target_domains
        and isinstance(report_fault.get("drained_node"), str)
        and bool(report_fault.get("drained_node"))
        and report_fault.get("node_failover_passed") is True
        and report_fault.get("zone_failover_passed") is True
        and report_fault.get("beat_recovery_passed") is True
        and report_fault.get("beat_original_node") != report_fault.get("beat_recovery_node")
        and int(report_fault.get("recovery_seconds", target_rto + 1)) <= target_rto
        and isinstance(report_probe, dict)
        and report_probe.get("transport") == "network HTTPS against target"
        and str(report_probe.get("base_url", "")).rstrip("/") == str(target.get("public_base_url", "")).rstrip("/")
        and report_probe.get("passed") is True
        and int(report_probe.get("sample_count", 0)) >= 5
        and int(report_probe.get("failure_count", -1)) == 0
        and isinstance(report_network, dict)
        and report_network.get("enforcement_exercised") is True
        and report_network.get("passed") is True
        and isinstance(report_state, dict)
        and report_state.get("managed_mysql_ha") is True
        and report_state.get("managed_redis_ha") is True
        and report_state.get("object_store_ha") is True
        and report_state.get("rwx_repository_storage_ha") is True
        and report_state.get("failover_exercised") is True
        and report_state.get("data_integrity_passed") is True
    )
    ha_ok = (
        ha.get("status") == "PASS"
        and isinstance(replica_counts, dict)
        and all(int(replica_counts.get(name, 0)) >= 3 for name in ("backend", "frontend", "worker"))
        and int(ha.get("fault_domains_exercised", 0)) >= 2
        and ha.get("managed_mysql_ha") is True
        and ha.get("managed_redis_ha") is True
        and ha.get("object_store_ha") is True
        and ha.get("rwx_repository_storage_ha") is True
        and ha.get("node_failover_passed") is True
        and ha.get("zone_failover_passed") is True
        and ha.get("beat_recovery_passed") is True
        and report_matches_target
    )
    gate.add(
        "high_availability",
        owner="Architecture",
        passed=ha_ok,
        observed=(
            f"status={ha.get('status')}, replicas={replica_counts}, "
            f"zones={ha.get('fault_domains_exercised')}, "
            f"report_scope={ha_report.get('scope') if isinstance(ha_report, dict) else 'missing'}"
        ),
        expected="3x stateless replicas; 2+ zones; HA state stores/RWX; node+zone+beat failover passed",
        detail="The content-addressed report must bind the exact target, commit and images; prove target HTTPS continuity, node/zone/Beat failover, restored spread, enforced policy and state-service integrity. Local kind evidence cannot authorize production.",
    )

    security = controls["security_assessment"]
    security_report = _evidence_json(security, authorization_path)
    assessment_report = (
        security_report.get("assessment")
        if isinstance(security_report, dict) and isinstance(security_report.get("assessment"), dict)
        else {}
    )
    findings_report = (
        security_report.get("findings")
        if isinstance(security_report, dict) and isinstance(security_report.get("findings"), dict)
        else {}
    )
    signed_report = (
        security_report.get("signed_report")
        if isinstance(security_report, dict) and isinstance(security_report.get("signed_report"), dict)
        else {}
    )
    assessment_scope = assessment_report.get("scope")
    methodologies = assessment_report.get("methodologies")
    assessment_scope_values = (
        set(assessment_scope)
        if isinstance(assessment_scope, list) and all(isinstance(item, str) for item in assessment_scope)
        else set()
    )
    methodology_values = (
        set(methodologies)
        if isinstance(methodologies, list) and all(isinstance(item, str) for item in methodologies)
        else set()
    )
    required_assessment_scope = {
        "application-and-api",
        "identity-and-access",
        "kubernetes-infrastructure",
        "supply-chain",
        "agent-security",
    }
    signed_report_ok, signed_report_detail = _verify_signed_evidence_file(
        signed_report,
        authorization_path=authorization_path,
        namespace="duckdock-security-assessment",
    )
    gate.add(
        "security_assessment_signature",
        owner="Security",
        passed=signed_report_ok,
        observed=signed_report_detail,
        expected="digest-matched assessor report with valid OpenSSH signature",
        detail="The independent assessor, not the DuckDock implementation team, must sign the retained report in namespace duckdock-security-assessment.",
    )
    security_report_matches = (
        _report_release_target_binding(
            security_report,
            schema_version="duckdock-ga-independent-security-evidence-v1",
            status="PASS",
            control=security,
            target=target,
            release=release,
        )
        and security_report.get("independent") is security.get("independent")
        and security_report.get("provider") == security.get("provider")
        and security_report.get("open_critical") == security.get("open_critical")
        and security_report.get("open_high") == security.get("open_high")
        and security_report.get("contract_digest") == release.get("contract_digest")
        and assessment_report.get("independence_attested") is True
        and _meaningful_string(assessment_report.get("assessment_id"))
        and isinstance(assessment_scope, list)
        and required_assessment_scope.issubset(assessment_scope_values)
        and isinstance(methodologies, list)
        and {"penetration-test", "manual-code-review"}.issubset(methodology_values)
        and _ordered_report_times(
            assessment_report.get("started_at"),
            assessment_report.get("completed_at"),
            no_later_than=_control_observed_at(security),
        )
        and findings_report.get("open_critical") == security.get("open_critical")
        and findings_report.get("open_high") == security.get("open_high")
        and findings_report.get("retest_completed") is True
        and bool(DIGEST_RE.fullmatch(str(signed_report.get("sha256", ""))))
        and _meaningful_string(signed_report.get("signer_identity"))
    )
    security_ok = (
        security.get("status") == "PASS"
        and security.get("independent") is True
        and isinstance(security.get("provider"), str)
        and not any(marker in str(security.get("provider")) for marker in PLACEHOLDER_MARKERS)
        and int(security.get("open_critical", -1)) == 0
        and int(security.get("open_high", -1)) == 0
        and security.get("source_commit") == commit
        and security.get("backend_image") == release.get("backend_image")
        and security.get("frontend_image") == release.get("frontend_image")
        and security_report_matches
        and signed_report_ok
    )
    gate.add(
        "security_assessment",
        owner="Security",
        passed=security_ok,
        observed=f"status={security.get('status')}, independent={security.get('independent')}, C={security.get('open_critical')}, H={security.get('open_high')}",
        expected="independent scoped assessment; 0 open critical/high; exact commit and image digests",
        detail="The independent signed report must bind the exact target, contract, commit and images; cover application, IAM, Kubernetes, supply-chain and agent risks; and record completed retesting.",
    )

    release_digest = _release_digest(release, target, controls)
    approvals = _list(document["approvals"], "approvals")
    approval_roles = [item.get("role") for item in approvals if isinstance(item, dict)]
    identities = [item.get("identity") for item in approvals if isinstance(item, dict)]
    gate.add(
        "approval_roles",
        owner="Product",
        passed=set(approval_roles) == REQUIRED_APPROVAL_ROLES and len(approvals) == 4,
        observed=approval_roles,
        expected=", ".join(sorted(REQUIRED_APPROVAL_ROLES)),
        detail="Exactly one approval is required from every accountable function.",
    )
    gate.add(
        "approval_four_eyes",
        owner="Security",
        passed=len(identities) == 4 and len({str(item) for item in identities}) == 4,
        observed=identities,
        expected="four distinct signer identities",
        detail="One person cannot satisfy multiple organizational approvals.",
    )
    latest_evidence = max(evidence_times, default=None)
    for role in sorted(REQUIRED_APPROVAL_ROLES):
        matches = [item for item in approvals if isinstance(item, dict) and item.get("role") == role]
        approval = matches[0] if len(matches) == 1 else {}
        identity = str(approval.get("identity", ""))
        approved_at = _parse_time(approval.get("approved_at"))
        allowed_signers = _resolve_file(approval.get("allowed_signers_path"), authorization_path)
        signature = _resolve_file(approval.get("signature_path"), authorization_path)
        signature_ok = False
        signature_detail = "missing signature artifacts"
        if allowed_signers and allowed_signers.is_file() and signature and signature.is_file() and identity:
            signature_ok, signature_detail = _verify_ssh_signature(
                identity=identity,
                allowed_signers=allowed_signers,
                signature=signature,
                release_digest=release_digest,
            )
        approval_ok = (
            approval.get("decision") == "APPROVED"
            and approval.get("signed_digest") == release_digest
            and approved_at is not None
            and latest_evidence is not None
            and approved_at >= latest_evidence
            and approved_at <= current
            and signature_ok
        )
        gate.add(
            f"approval_{role}",
            owner=role,
            passed=approval_ok,
            observed=f"identity={identity or 'missing'}, approved_at={approved_at}, signature={signature_detail}",
            expected=f"APPROVED after latest evidence; OpenSSH signature over {release_digest}",
            detail="Approvals are cryptographically bound to the release, target and every evidence digest.",
        )

    failed = {check.key for check in gate.checks if not check.passed}
    if not failed:
        status = "GA_AUTHORIZED"
    elif failed.issubset(EXTERNAL_CHECK_KEYS | {f"{key}_evidence" for key in EXTERNAL_CHECK_KEYS}):
        status = "AWAITING_EXTERNAL_APPROVALS"
    else:
        status = "BLOCKED"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "release_digest": release_digest,
        "checked_at": current.isoformat(),
        "pass_count": sum(check.passed for check in gate.checks),
        "block_count": sum(not check.passed for check in gate.checks),
        "checks": [{**asdict(check), "status": "PASS" if check.passed else "BLOCK"} for check in gate.checks],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("authorization", type=Path)
    parser.add_argument("--lint", action="store_true", help="validate structure only; placeholders are allowed")
    parser.add_argument("--output", type=Path, help="write the JSON evaluation report")
    parser.add_argument("--allow-blocked", action="store_true", help="return zero after emitting a non-GA report")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = _load_json(args.authorization)
        lint_errors = lint_authorization(document)
        if lint_errors:
            raise ValueError("; ".join(lint_errors))
        if args.lint:
            print(json.dumps({"ok": True, "schema_version": SCHEMA_VERSION}, sort_keys=True))
            return 0
        result = evaluate(document, authorization_path=args.authorization.resolve())
    except ValueError as exc:
        print(f"GA authorization error: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if result["status"] == "GA_AUTHORIZED" or args.allow_blocked:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
