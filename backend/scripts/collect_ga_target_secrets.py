#!/usr/bin/env python3
"""Collect signed target secret-rotation evidence without reading secret values.

The collector observes Kubernetes metadata before and after an externally
performed rotation.  The secret provider and an independent verifier must
write OpenSSH-signed receipts after the exercise starts.  No credential value
is accepted on the command line, read from the Kubernetes Secret, or retained
in the resulting report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from scripts.ga_release_identity import build_release_binding
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_identity import build_release_binding


SCHEMA_VERSION = "duckdock-ga-secrets-evidence-v2"
POLICY_SCHEMA_VERSION = "duckdock-ga-secrets-trust-policy-v1"
ROTATION_SCHEMA_VERSION = "duckdock-ga-secret-rotation-receipt-v1"
VERIFICATION_SCHEMA_VERSION = "duckdock-ga-secret-verification-receipt-v1"
ROTATION_SIGNATURE_NAMESPACE = "duckdock-secret-rotation-receipt"
VERIFICATION_SIGNATURE_NAMESPACE = "duckdock-secret-verification-receipt"
WORKLOAD_COMPONENTS = ("backend", "worker", "beat")
REQUIRED_SECRET_CLASSES = ("application-signing", "database", "object-store")
EXERCISE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
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
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "access_key",
        "api_key",
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
        return any(
            str(key).lower() in forbidden or _contains_secret_material_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_material_key(item) for item in value)
    return False


def _allowed_signer_bindings(path: Path) -> dict[str, set[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read secrets allowed-signers: {exc}") from exc
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
                f"secrets allowed-signers line {line_number} has no OpenSSH public key"
            )
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not _meaningful(principal) or any(marker in principal for marker in "*?!"):
                raise ValueError(
                    f"secrets allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("secrets allowed-signers contains no principals")
    owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            owners.setdefault(key, set()).add(principal)
    if any(len(principals) != 1 for principals in owners.values()):
        raise ValueError("secrets allowed-signers reuses a public key across identities")
    return bindings


def load_secrets_policy(
    path: Path,
    *,
    provider: str,
    provider_identity: str,
    verifier_identity: str,
) -> tuple[dict[str, Any], Path, str]:
    policy = _load_object(path, "secrets trust policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "approved_providers",
        "provider_identities",
        "verifier_identities",
        "required_secret_classes",
        "workload_components",
    }
    provider_identities = policy.get("provider_identities")
    verifier_identities = policy.get("verifier_identities")
    approved_providers = policy.get("approved_providers")
    required_classes = policy.get("required_secret_classes")
    components = policy.get("workload_components")
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == POLICY_SCHEMA_VERSION
        and _meaningful(policy.get("policy_id"))
        and _meaningful(policy.get("organization"))
        and isinstance(approved_providers, list)
        and bool(approved_providers)
        and all(_meaningful(item) for item in approved_providers)
        and len(approved_providers) == len(set(approved_providers))
        and provider in approved_providers
        and isinstance(provider_identities, list)
        and bool(provider_identities)
        and all(_meaningful(item) for item in provider_identities)
        and len(provider_identities) == len(set(provider_identities))
        and provider_identity in provider_identities
        and isinstance(verifier_identities, list)
        and bool(verifier_identities)
        and all(_meaningful(item) for item in verifier_identities)
        and len(verifier_identities) == len(set(verifier_identities))
        and verifier_identity in verifier_identities
        and not set(provider_identities).intersection(verifier_identities)
        and provider_identity != verifier_identity
        and required_classes == list(REQUIRED_SECRET_CLASSES)
        and components == list(WORKLOAD_COMPONENTS)
    ):
        raise ValueError("secrets trust policy does not authorize the requested exercise")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("secrets trust policy has no allowed_signers_path")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_digest = str(policy.get("allowed_signers_sha256", ""))
    if (
        not trust_path.is_file()
        or not DIGEST_RE.fullmatch(expected_digest)
        or _sha256(trust_path) != expected_digest
    ):
        raise ValueError("secrets trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    configured = set(provider_identities).union(verifier_identities)
    if set(bindings) != configured:
        raise ValueError("secrets trust store principals do not exactly match policy identities")
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
        raise ValueError(f"cannot verify signed secrets receipt: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid secrets receipt signature: {detail}")


def _class_rows_valid(rows: Any, *, verification: bool) -> bool:
    if not isinstance(rows, list) or len(rows) != len(REQUIRED_SECRET_CLASSES):
        return False
    if [row.get("name") for row in rows if isinstance(row, dict)] != list(
        REQUIRED_SECRET_CLASSES
    ):
        return False
    seen_receipts: set[str] = set()
    seen_audit_events: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or _contains_secret_material_key(row):
            return False
        if verification:
            if set(row) != {
                "name",
                "probe_receipt_id",
                "tested_at",
                "previous_version_rejected",
                "new_version_accepted",
                "audit_event_id",
            }:
                return False
            receipt_id = row.get("probe_receipt_id")
            audit_event_id = row.get("audit_event_id")
            if (
                not _meaningful(receipt_id)
                or receipt_id in seen_receipts
                or not _meaningful(audit_event_id)
                or audit_event_id in seen_audit_events
                or _parse_time(row.get("tested_at")) is None
                or row.get("previous_version_rejected") is not True
                or row.get("new_version_accepted") is not True
            ):
                return False
        else:
            if set(row) != {
                "name",
                "previous_version_id",
                "new_version_id",
                "provider_receipt_id",
                "audit_event_id",
                "rotated_at",
                "old_version_disabled_at",
            }:
                return False
            receipt_id = row.get("provider_receipt_id")
            previous = row.get("previous_version_id")
            current = row.get("new_version_id")
            rotated = _parse_time(row.get("rotated_at"))
            disabled = _parse_time(row.get("old_version_disabled_at"))
            audit_event_id = row.get("audit_event_id")
            if (
                not _meaningful(receipt_id)
                or receipt_id in seen_receipts
                or not _meaningful(previous)
                or not _meaningful(current)
                or previous == current
                or not _meaningful(audit_event_id)
                or audit_event_id in seen_audit_events
                or rotated is None
                or disabled is None
                or rotated > disabled
            ):
                return False
        seen_receipts.add(str(receipt_id))
        seen_audit_events.add(str(audit_event_id))
    return True


def validate_rotation_receipt(
    receipt: dict[str, Any],
    *,
    exercise_id: str,
    target_environment: str,
    provider: str,
    secret_name: str,
) -> datetime:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "provider",
        "secret_name",
        "started_at",
        "completed_at",
        "encrypted_at_rest",
        "access_audit_enabled",
        "credentials_external_to_evidence",
        "secret_classes",
    }
    started = _parse_time(receipt.get("started_at"))
    completed = _parse_time(receipt.get("completed_at"))
    rows = receipt.get("secret_classes")
    row_times = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                row_times.extend(
                    [
                        _parse_time(row.get("rotated_at")),
                        _parse_time(row.get("old_version_disabled_at")),
                    ]
                )
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == ROTATION_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("target_environment") == target_environment
        and receipt.get("provider") == provider
        and receipt.get("secret_name") == secret_name
        and receipt.get("encrypted_at_rest") is True
        and receipt.get("access_audit_enabled") is True
        and receipt.get("credentials_external_to_evidence") is True
        and started is not None
        and completed is not None
        and started <= completed
        and _class_rows_valid(rows, verification=False)
        and row_times
        and all(value is not None and started <= value <= completed for value in row_times)
        and not _contains_secret_material_key(receipt)
    ):
        raise ValueError("invalid signed secret rotation receipt")
    return completed


def validate_verification_receipt(
    receipt: dict[str, Any],
    *,
    exercise_id: str,
    target_environment: str,
    secret_name: str,
) -> datetime:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "secret_name",
        "started_at",
        "completed_at",
        "secret_classes",
        "all_passed",
    }
    started = _parse_time(receipt.get("started_at"))
    completed = _parse_time(receipt.get("completed_at"))
    rows = receipt.get("secret_classes")
    tested = [
        _parse_time(row.get("tested_at"))
        for row in rows or []
        if isinstance(row, dict)
    ]
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == VERIFICATION_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("target_environment") == target_environment
        and receipt.get("secret_name") == secret_name
        and receipt.get("all_passed") is True
        and started is not None
        and completed is not None
        and started <= completed
        and _class_rows_valid(rows, verification=True)
        and tested
        and all(value is not None and started <= value <= completed for value in tested)
        and not _contains_secret_material_key(receipt)
    ):
        raise ValueError("invalid signed secret verification receipt")
    return completed


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
                receipt = _load_object(path, "signed secrets receipt")
                verify_signature(
                    path,
                    signature,
                    identity=identity,
                    allowed_signers=allowed_signers,
                    namespace=namespace,
                )
                completed = validator(receipt)
            except ValueError as exc:
                last_error = str(exc)
            else:
                return receipt, completed
        time.sleep(min(0.25, max(timeout_seconds, 0.01)))
    raise RuntimeError(f"timed out waiting for signed secrets receipt: {last_error}")


class Kubectl:
    def __init__(self, context: str, namespace: str) -> None:
        self.context = context
        self.namespace = namespace

    def text(
        self,
        arguments: Sequence[str],
        *,
        namespaced: bool = False,
        timeout: float = 600,
    ) -> str:
        command = ["kubectl", "--context", self.context]
        if namespaced:
            command.extend(["--namespace", self.namespace])
        command.extend(arguments)
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"kubectl failed ({' '.join(arguments)}): {completed.stdout.strip()}"
            )
        return completed.stdout.strip()


SECRET_JSONPATH = (
    '{.metadata.name}{"\\t"}{.metadata.uid}{"\\t"}'
    '{.metadata.resourceVersion}{"\\t"}{.metadata.creationTimestamp}'
)
DEPLOYMENT_JSONPATH = (
    '{.metadata.name}{"\\t"}{.metadata.uid}{"\\t"}{.metadata.resourceVersion}{"\\t"}'
    '{.metadata.generation}{"\\t"}{.spec.replicas}{"\\t"}{.status.observedGeneration}{"\\t"}'
    '{.status.updatedReplicas}{"\\t"}{.status.readyReplicas}{"\\t"}{.status.availableReplicas}'
)
PODS_JSONPATH = (
    '{range .items[*]}{.metadata.name}{"\\t"}{.metadata.uid}{"\\t"}'
    '{.metadata.resourceVersion}{"\\t"}{.metadata.creationTimestamp}{"\\t"}'
    '{.metadata.deletionTimestamp}{"\\t"}{.status.phase}{"\\t"}'
    '{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\\n"}{end}'
)


def _split_row(value: str, *, fields: int, label: str) -> list[str]:
    parts = value.split("\t")
    if len(parts) != fields or not all(parts[index] for index in range(fields)):
        raise ValueError(f"invalid metadata-only kubectl {label} output")
    return parts


def _positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"invalid integer in {label} metadata") from exc
    if parsed < 1:
        raise ValueError(f"{label} metadata must be positive")
    return parsed


def _secret_snapshot(runner: Kubectl, secret_name: str) -> dict[str, Any]:
    parts = _split_row(
        runner.text(
            ["get", "secret", secret_name, "-o", f"jsonpath={SECRET_JSONPATH}"],
            namespaced=True,
        ),
        fields=4,
        label="secret",
    )
    if parts[0] != secret_name or not all(_meaningful(item) for item in parts):
        raise ValueError("secret metadata identity is incomplete")
    return {
        "name": parts[0],
        "uid": parts[1],
        "resource_version": parts[2],
        "creation_timestamp": parts[3],
    }


def _deployment_snapshot(runner: Kubectl, component: str) -> dict[str, Any]:
    parts = _split_row(
        runner.text(
            ["get", "deployment", component, "-o", f"jsonpath={DEPLOYMENT_JSONPATH}"],
            namespaced=True,
        ),
        fields=9,
        label=f"deployment/{component}",
    )
    if parts[0] != component or not _meaningful(parts[1]) or not _meaningful(parts[2]):
        raise ValueError(f"deployment/{component} metadata identity is incomplete")
    return {
        "name": parts[0],
        "uid": parts[1],
        "resource_version": parts[2],
        "generation": _positive_int(parts[3], f"deployment/{component} generation"),
        "desired_replicas": _positive_int(parts[4], f"deployment/{component} replicas"),
        "observed_generation": _positive_int(
            parts[5], f"deployment/{component} observed generation"
        ),
        "updated_replicas": _positive_int(parts[6], f"deployment/{component} updated replicas"),
        "ready_replicas": _positive_int(parts[7], f"deployment/{component} ready replicas"),
        "available_replicas": _positive_int(
            parts[8], f"deployment/{component} available replicas"
        ),
    }


def _pod_snapshot(runner: Kubectl, component: str) -> list[dict[str, Any]]:
    output = runner.text(
        [
            "get",
            "pods",
            "-l",
            f"app.kubernetes.io/name=duckdock,app.kubernetes.io/component={component}",
            "-o",
            f"jsonpath={PODS_JSONPATH}",
        ],
        namespaced=True,
    )
    rows: list[dict[str, Any]] = []
    for raw in output.splitlines():
        parts = raw.split("\t")
        if len(parts) != 7 or not all(parts[index] for index in (0, 1, 2, 3, 5, 6)):
            raise ValueError(f"invalid metadata-only pod output for {component}")
        rows.append(
            {
                "name": parts[0],
                "uid": parts[1],
                "resource_version": parts[2],
                "creation_timestamp": parts[3],
                "deletion_timestamp": parts[4] or None,
                "phase": parts[5],
                "ready": parts[6] == "True",
            }
        )
    if not rows or len({row["uid"] for row in rows}) != len(rows):
        raise ValueError(f"pod identities for {component} are empty or duplicated")
    return sorted(rows, key=lambda row: row["name"])


def kubernetes_snapshot(
    runner: Kubectl,
    *,
    secret_name: str,
) -> dict[str, Any]:
    workloads: dict[str, Any] = {}
    for component in WORKLOAD_COMPONENTS:
        workloads[component] = {
            "deployment": _deployment_snapshot(runner, component),
            "pods": _pod_snapshot(runner, component),
        }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "secret": _secret_snapshot(runner, secret_name),
        "workloads": workloads,
    }


def _workload_ready(value: dict[str, Any]) -> bool:
    deployment = value.get("deployment")
    pods = value.get("pods")
    if not isinstance(deployment, dict) or not isinstance(pods, list):
        return False
    desired = deployment.get("desired_replicas")
    ready_pods = [
        pod
        for pod in pods
        if isinstance(pod, dict)
        and pod.get("ready") is True
        and pod.get("phase") == "Running"
        and pod.get("deletion_timestamp") is None
    ]
    return (
        isinstance(desired, int)
        and not isinstance(desired, bool)
        and desired >= 1
        and deployment.get("observed_generation") == deployment.get("generation")
        and deployment.get("updated_replicas") == desired
        and deployment.get("ready_replicas") == desired
        and deployment.get("available_replicas") == desired
        and len(ready_pods) == desired
        and len(pods) == desired
    )


def validate_initial_snapshot(snapshot: dict[str, Any]) -> None:
    workloads = snapshot.get("workloads")
    if not (
        _parse_time(snapshot.get("captured_at")) is not None
        and isinstance(snapshot.get("secret"), dict)
        and isinstance(workloads, dict)
        and set(workloads) == set(WORKLOAD_COMPONENTS)
        and all(_workload_ready(workloads[component]) for component in WORKLOAD_COMPONENTS)
    ):
        raise ValueError("target workloads are not fully ready before secret rotation")


def rotation_state(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_secret = before.get("secret", {})
    after_secret = after.get("secret", {})
    before_workloads = before.get("workloads", {})
    after_workloads = after.get("workloads", {})
    component_results: dict[str, Any] = {}
    for component in WORKLOAD_COMPONENTS:
        old = before_workloads.get(component, {})
        new = after_workloads.get(component, {})
        old_deployment = old.get("deployment", {})
        new_deployment = new.get("deployment", {})
        old_uids = {
            pod.get("uid") for pod in old.get("pods", []) if isinstance(pod, dict)
        }
        new_uids = {
            pod.get("uid") for pod in new.get("pods", []) if isinstance(pod, dict)
        }
        component_results[component] = {
            "deployment_uid_unchanged": old_deployment.get("uid")
            == new_deployment.get("uid"),
            "deployment_generation_advanced": isinstance(
                old_deployment.get("generation"), int
            )
            and isinstance(new_deployment.get("generation"), int)
            and new_deployment["generation"] > old_deployment["generation"],
            "all_old_pods_replaced": bool(old_uids)
            and bool(new_uids)
            and old_uids.isdisjoint(new_uids),
            "fully_ready": _workload_ready(new),
        }
    return {
        "secret_uid_unchanged": before_secret.get("uid") == after_secret.get("uid"),
        "secret_resource_version_changed": _meaningful(before_secret.get("resource_version"))
        and _meaningful(after_secret.get("resource_version"))
        and before_secret.get("resource_version") != after_secret.get("resource_version"),
        "components": component_results,
    }


def _rotation_state_passed(state: dict[str, Any]) -> bool:
    components = state.get("components")
    return (
        state.get("secret_uid_unchanged") is True
        and state.get("secret_resource_version_changed") is True
        and isinstance(components, dict)
        and set(components) == set(WORKLOAD_COMPONENTS)
        and all(
            isinstance(item, dict)
            and item.get("deployment_uid_unchanged") is True
            and item.get("deployment_generation_advanced") is True
            and item.get("all_old_pods_replaced") is True
            and item.get("fully_ready") is True
            for item in components.values()
        )
    )


def wait_for_rotation_state(
    runner: Kubectl,
    *,
    before: dict[str, Any],
    secret_name: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        after = kubernetes_snapshot(runner, secret_name=secret_name)
        last_state = rotation_state(before, after)
        if _rotation_state_passed(last_state):
            return after, last_state
        time.sleep(min(0.25, max(timeout_seconds, 0.01)))
    raise RuntimeError(f"timed out waiting for target workload reload: {last_state}")


def _signed_reference(path: Path, signature: Path, *, identity: str) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "signature_path": str(signature.resolve()),
        "signer_identity": identity,
    }


def collect(args: argparse.Namespace, *, kubectl: Kubectl | None = None) -> dict[str, Any]:
    receipt_paths = (
        args.rotation_receipt,
        args.rotation_signature,
        args.verification_receipt,
        args.verification_signature,
    )
    if any(path.exists() for path in receipt_paths):
        raise ValueError("secrets receipt/signature paths must not exist before the exercise")
    policy, allowed_signers, policy_digest = load_secrets_policy(
        args.secrets_policy,
        provider=args.provider,
        provider_identity=args.provider_signer_identity,
        verifier_identity=args.verifier_signer_identity,
    )
    runner = kubectl or Kubectl(args.context, args.namespace)
    if runner.text(["config", "get-contexts", args.context, "-o", "name"]) != args.context:
        raise ValueError("requested kube context does not exist exactly")
    for resource in (f"secret/{args.secret_name}", "deployments", "pods"):
        if runner.text(["auth", "can-i", "get", resource], namespaced=True) != "yes":
            raise ValueError(f"kube identity cannot get {resource}")

    before = kubernetes_snapshot(runner, secret_name=args.secret_name)
    validate_initial_snapshot(before)
    exercise_started = datetime.now(timezone.utc)
    print(
        f"Initial metadata snapshot captured for {args.target_environment}; begin external "
        f"secret rotation exercise {args.exercise_id} now.",
        file=sys.stderr,
        flush=True,
    )
    rotation_receipt, rotation_completed = wait_signed_receipt(
        args.rotation_receipt,
        args.rotation_signature,
        identity=args.provider_signer_identity,
        allowed_signers=allowed_signers,
        namespace=ROTATION_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_rotation_receipt(
            receipt,
            exercise_id=args.exercise_id,
            target_environment=args.target_environment,
            provider=args.provider,
            secret_name=args.secret_name,
        ),
    )
    verification_receipt, verification_completed = wait_signed_receipt(
        args.verification_receipt,
        args.verification_signature,
        identity=args.verifier_signer_identity,
        allowed_signers=allowed_signers,
        namespace=VERIFICATION_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_verification_receipt(
            receipt,
            exercise_id=args.exercise_id,
            target_environment=args.target_environment,
            secret_name=args.secret_name,
        ),
    )
    if (
        rotation_completed < exercise_started - timedelta(minutes=5)
        or verification_completed < exercise_started - timedelta(minutes=5)
        or _parse_time(verification_receipt.get("started_at")) < rotation_completed
        or verification_completed < rotation_completed
    ):
        raise ValueError("signed secrets receipts are stale or out of exercise order")

    after, state = wait_for_rotation_state(
        runner,
        before=before,
        secret_name=args.secret_name,
        timeout_seconds=args.rollout_timeout,
    )
    observed_at = datetime.now(timezone.utc)
    if max(rotation_completed, verification_completed) > observed_at + timedelta(minutes=5):
        raise ValueError("signed secrets receipt completion time is in the future")
    passed = _rotation_state_passed(state)
    report = {
        "schema_version": SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS" if passed else "BLOCK",
        "passed": passed,
        "observed_at": observed_at.isoformat(),
        "provider": args.provider,
        "plaintext_env_persisted": False,
        "rotation_tested": True,
        "cluster_context": args.context,
        "namespace": args.namespace,
        "secrets_policy": {
            "path": str(args.secrets_policy.resolve()),
            "sha256": policy_digest,
            "policy_id": policy["policy_id"],
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": _sha256(allowed_signers),
        },
        "secret_store": {
            "encrypted_at_rest": rotation_receipt["encrypted_at_rest"],
            "access_audit_enabled": rotation_receipt["access_audit_enabled"],
            "credentials_external_to_evidence": rotation_receipt[
                "credentials_external_to_evidence"
            ],
        },
        "rotation": {
            "exercise_id": args.exercise_id,
            "secret_name": args.secret_name,
            "secret_classes": list(REQUIRED_SECRET_CLASSES),
            "started_at": exercise_started.isoformat(),
            "completed_at": observed_at.isoformat(),
            "old_credentials_rejected": True,
            "new_credentials_accepted": True,
            "workloads_reloaded": passed,
            "audit_event_recorded": True,
        },
        "kubernetes_observation": {
            "metadata_only": True,
            "secret_data_read": False,
            "before": before,
            "after": after,
            "verification": state,
        },
        "rotation_receipt": {
            **rotation_receipt,
            "signed_evidence": _signed_reference(
                args.rotation_receipt,
                args.rotation_signature,
                identity=args.provider_signer_identity,
            ),
        },
        "verification_receipt": {
            **verification_receipt,
            "signed_evidence": _signed_reference(
                args.verification_receipt,
                args.verification_signature,
                identity=args.verifier_signer_identity,
            ),
        },
    }
    if _contains_secret_material_key(report):
        raise ValueError("secrets report contains a forbidden secret-material field")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="exact kubeconfig context")
    parser.add_argument("--namespace", default="duckdock")
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--acknowledge-target-rotation", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--secret-name", default="duckdock-runtime-secrets")
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--secrets-policy", type=Path, required=True)
    parser.add_argument("--provider-signer-identity", required=True)
    parser.add_argument("--verifier-signer-identity", required=True)
    parser.add_argument("--rotation-receipt", type=Path, required=True)
    parser.add_argument("--rotation-signature", type=Path, required=True)
    parser.add_argument("--verification-receipt", type=Path, required=True)
    parser.add_argument("--verification-signature", type=Path, required=True)
    parser.add_argument("--receipt-timeout", type=float, default=900.0)
    parser.add_argument("--rollout-timeout", type=float, default=900.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.acknowledge_target_rotation != args.target_environment:
            raise ValueError("--acknowledge-target-rotation must exactly equal target environment")
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe, non-placeholder characters")
        if not all(
            _meaningful(value)
            for value in (
                args.context,
                args.namespace,
                args.target_environment,
                args.provider,
                args.secret_name,
                args.provider_signer_identity,
                args.verifier_signer_identity,
            )
        ):
            raise ValueError("target, provider, secret and signer identities must be non-placeholder")
        if args.provider_signer_identity == args.verifier_signer_identity:
            raise ValueError("provider and verifier signer identities must be distinct")
        if min(args.receipt_timeout, args.rollout_timeout) <= 0:
            raise ValueError("receipt and rollout timeouts must be positive")
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new path or pass --overwrite-output")
        if not args.secrets_policy.is_file():
            raise ValueError("secrets policy does not exist")
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
    except (OSError, UnicodeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Target secrets collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
