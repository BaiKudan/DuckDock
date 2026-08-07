#!/usr/bin/env python3
"""Preflight or deploy a prepared DuckDock Kubernetes HA target bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SCRIPT = REPO_ROOT / "scripts" / "prepare-kubernetes-ha-target.py"
PREFLIGHT_SCHEMA_VERSION = "duckdock-kubernetes-ha-target-preflight-v1"
DEPLOYMENT_SCHEMA_VERSION = "duckdock-kubernetes-ha-target-deployment-v1"
PREFLIGHT_STATUS = "TARGET_HA_PREFLIGHT_PASSED_NOT_GA_AUTHORIZED"
DEPLOYED_STATUS = "TARGET_HA_DEPLOYMENT_COMPLETED_NOT_GA_AUTHORIZED"
INCOMPLETE_STATUS = "TARGET_HA_DEPLOYMENT_INCOMPLETE_NOT_GA_AUTHORIZED"
REFUSED_STATUS = "TARGET_HA_DEPLOYMENT_REFUSED_NOT_MUTATED"
AUTHORIZATION_BOUNDARY = (
    "operational target preparation only; does not prove release provenance, "
    "managed-state HA, fault-domain survival, independent security assessment "
    "or GA authorization"
)
PREFLIGHT_MAX_AGE = timedelta(hours=1)
MINIMUM_KUBERNETES = (1, 27)
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,127}$")
REQUIRED_RUNTIME_SECRET_KEYS = {
    "AGENT_EXECUTION_INGESTION_ENABLED",
    "BACKEND_BASE_URL",
    "CORS_ORIGINS",
    "DATABASE_URL",
    "DEBUG",
    "DUCKDOCK_CREDENTIAL_KEY",
    "MINIO_ACCESS_KEY",
    "MINIO_BUCKET",
    "MINIO_ENDPOINT",
    "MINIO_PUBLIC_ENDPOINT",
    "MINIO_SECRET_KEY",
    "MINIO_SECURE",
    "REDIS_URL",
    "REPOS_ROOT",
    "SECRET_KEY",
}
REQUIRED_TLS_SECRET_KEYS = {"tls.crt", "tls.key"}
REPLICATED_DEPLOYMENTS = {"backend", "frontend", "worker"}
ALL_DEPLOYMENTS = (*sorted(REPLICATED_DEPLOYMENTS), "beat")
PENDING_AFTER_DEPLOYMENT = {
    "release_image_provenance": "PENDING_EXTERNAL",
    "runtime_secret_values_and_rotation": "PENDING_EXTERNAL",
    "tls_certificate_external_probe": "PENDING_EXTERNAL",
    "network_policy_enforcement": "PENDING_EXTERNAL",
    "rwx_storage_redundancy": "PENDING_EXTERNAL",
    "managed_mysql_redis_s3_ha": "PENDING_EXTERNAL",
    "target_capacity_and_data_growth": "PENDING_EXTERNAL",
    "backup_restore_and_oncall": "PENDING_EXTERNAL",
    "fault_domain_exercise": "PENDING_EXTERNAL",
    "independent_security_assessment": "PENDING_EXTERNAL",
    "four_role_ga_approvals": "PENDING_EXTERNAL",
}
PREFLIGHT_VALIDATED_REQUIREMENTS = {
    "runtime_secret_metadata": "PASS",
    "tls_secret_metadata": "PASS",
    "ingress_and_monitoring_namespace_labels": "PASS",
    "server_side_dry_run": "PASS",
    "three_ready_zones": "PASS",
    "metrics_server": "PASS",
    "least_privilege_deployment_identity": "PASS",
}


class TargetError(ValueError):
    def __init__(self, message: str, *, stage: str = "validation") -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: bytes
    stderr: bytes
    started_at: str
    finished_at: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class PermissionCheck:
    expectation: str
    verb: str
    resource: str
    namespace: str | None = None


Runner = Callable[[list[str], int], CommandResult]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _bundle_api() -> Any:
    module_name = "duckdock_kubernetes_ha_target_bundle_runtime"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, BUNDLE_SCRIPT)
    if spec is None or spec.loader is None:
        raise TargetError("cannot load the HA target bundle verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def default_runner(argv: list[str], timeout_seconds: int) -> CommandResult:
    started = utc_now()
    started_monotonic = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetError(f"cannot execute kubectl: {type(exc).__name__}", stage="kubectl") from exc
    finished = utc_now()
    if len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES:
        raise TargetError("kubectl output exceeded the one MiB safety bound", stage="kubectl")
    return CommandResult(
        argv=list(argv),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_ms=round((time.monotonic() - started_monotonic) * 1000),
    )


def command_evidence(result: CommandResult, *, stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "argv": result.argv,
        "exit_code": result.exit_code,
        "stdout_sha256": sha256_bytes(result.stdout),
        "stderr_sha256": sha256_bytes(result.stderr),
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_ms": result.duration_ms,
    }


class Kubectl:
    def __init__(self, context: str, runner: Runner) -> None:
        self.context = context
        self.runner = runner
        self.commands: list[dict[str, Any]] = []

    def run(
        self,
        stage: str,
        arguments: Sequence[str],
        *,
        timeout_seconds: int = 60,
    ) -> bytes:
        argv = [
            "kubectl",
            "--context",
            self.context,
            "--request-timeout=30s",
            *arguments,
        ]
        result = self.runner(argv, timeout_seconds)
        self.commands.append(command_evidence(result, stage=stage))
        if result.exit_code != 0:
            raise TargetError(
                f"{stage} failed with kubectl exit code {result.exit_code}",
                stage=stage,
            )
        return result.stdout

    def json(
        self,
        stage: str,
        arguments: Sequence[str],
        *,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        payload = self.run(stage, arguments, timeout_seconds=timeout_seconds)
        try:
            value = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TargetError(f"{stage} returned invalid JSON", stage=stage) from exc
        if not isinstance(value, dict):
            raise TargetError(f"{stage} JSON root must be an object", stage=stage)
        return value


def _meaningful(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(marker in value.lower() for marker in ("change_me", "example", "placeholder"))
    )


def validate_context_value(value: str, *, field: str) -> str:
    if not _meaningful(value) or len(value) > 253 or not value.isprintable():
        raise TargetError(f"{field} must be a bounded non-placeholder string")
    return value


def validate_output(path: Path, *, bundle_dir: Path | None = None) -> Path:
    unresolved = path.expanduser()
    if unresolved.is_symlink():
        raise TargetError("receipt output must not be a symbolic link")
    resolved = unresolved.resolve(strict=False)
    sidecar = receipt_sidecar(resolved)
    if resolved.exists() or sidecar.exists():
        raise TargetError("receipt output and sidecar must not already exist")
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise TargetError("receipt output must be outside the repository")
    if bundle_dir is not None and (resolved == bundle_dir or bundle_dir in resolved.parents):
        raise TargetError("receipt output must be outside the prepared target bundle")
    if unresolved.parent.is_symlink():
        raise TargetError("receipt parent directory must not be a symbolic link")
    resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resolved.parent.stat().st_mode & 0o077:
        raise TargetError("receipt parent directory must not be accessible by group or others")
    return resolved


def receipt_sidecar(path: Path) -> Path:
    return Path(f"{path}.sha256")


def write_receipt(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise TargetError("receipt parent directory must not be accessible by group or others")
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    digest = sha256_bytes(body)
    sidecar = receipt_sidecar(path)
    wrote_path = False
    wrote_sidecar = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        wrote_path = True
        descriptor = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(f"{digest}  {path.name}\n".encode())
            handle.flush()
            os.fsync(handle.fileno())
        wrote_sidecar = True
    except Exception:
        if wrote_sidecar:
            sidecar.unlink(missing_ok=True)
        if wrote_path:
            path.unlink(missing_ok=True)
        raise
    return digest


def load_receipt(path: Path, *, expected_schema: str) -> dict[str, Any]:
    unresolved = path.expanduser()
    if unresolved.is_symlink():
        raise TargetError("receipt must not be a symbolic link")
    resolved = unresolved.resolve(strict=True)
    sidecar = receipt_sidecar(resolved)
    if not resolved.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise TargetError("receipt or SHA-256 sidecar is missing")
    if resolved.stat().st_mode & 0o077 or sidecar.stat().st_mode & 0o077:
        raise TargetError("receipt and sidecar must not be accessible by group or others")
    expected = f"{sha256_file(resolved)}  {resolved.name}\n"
    if sidecar.read_text(encoding="utf-8") != expected:
        raise TargetError("receipt SHA-256 sidecar does not match")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TargetError("receipt is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != expected_schema:
        raise TargetError("receipt schema is unsupported")
    if value.get("authorization_boundary") != AUTHORIZATION_BOUNDARY:
        raise TargetError("receipt authorization boundary changed")
    return value


def _metadata(value: dict[str, Any], *, label: str) -> dict[str, Any]:
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        raise TargetError(f"{label} lacks metadata", stage=label)
    return metadata


def observe_identity(
    kubectl: Kubectl,
    *,
    namespace: str,
    expected_cluster_uid: str,
    expected_principal: str,
) -> dict[str, str]:
    whoami = kubectl.json("cluster_identity_principal", ["auth", "whoami", "-o", "json"])
    kube_system = kubectl.json(
        "cluster_identity_uid",
        ["get", "namespace", "kube-system", "-o", "json"],
    )
    status = whoami.get("status")
    user_info = status.get("userInfo") if isinstance(status, dict) else None
    principal = user_info.get("username") if isinstance(user_info, dict) else None
    cluster_uid = _metadata(kube_system, label="kube-system Namespace").get("uid")
    observed = {
        "context": kubectl.context,
        "namespace": namespace,
        "cluster_uid": str(cluster_uid or ""),
        "principal": str(principal or ""),
    }
    if observed["cluster_uid"] != expected_cluster_uid:
        raise TargetError("live kube-system UID differs from the expected target", stage="cluster_identity")
    if observed["principal"] != expected_principal:
        raise TargetError("live Kubernetes principal differs from the expected operator", stage="cluster_identity")
    return observed


def kubernetes_version(kubectl: Kubectl) -> dict[str, Any]:
    value = kubectl.json("kubernetes_version", ["version", "-o", "json"])
    server = value.get("serverVersion")
    if not isinstance(server, dict):
        raise TargetError("kubectl version omitted serverVersion", stage="kubernetes_version")
    major_raw = str(server.get("major", ""))
    minor_raw = str(server.get("minor", ""))
    major_match = re.match(r"^[0-9]+", major_raw)
    minor_match = re.match(r"^[0-9]+", minor_raw)
    if major_match is None or minor_match is None:
        raise TargetError("Kubernetes server version is not numeric", stage="kubernetes_version")
    version = (int(major_match.group()), int(minor_match.group()))
    if version < MINIMUM_KUBERNETES:
        raise TargetError("Kubernetes 1.27 or newer is required", stage="kubernetes_version")
    return {
        "git_version": str(server.get("gitVersion", "")),
        "major": version[0],
        "minor": version[1],
    }


def validate_namespace(kubectl: Kubectl, namespace: str) -> dict[str, Any]:
    value = kubectl.json(
        "target_namespace",
        ["get", "namespace", namespace, "-o", "json"],
    )
    metadata = _metadata(value, label="target Namespace")
    labels = metadata.get("labels")
    required = {
        "pod-security.kubernetes.io/enforce": "restricted",
        "pod-security.kubernetes.io/audit": "restricted",
        "pod-security.kubernetes.io/warn": "restricted",
    }
    if not isinstance(labels, dict) or any(labels.get(key) != expected for key, expected in required.items()):
        raise TargetError("target Namespace must already enforce restricted Pod Security", stage="target_namespace")
    return {
        "name": namespace,
        "uid": str(metadata.get("uid", "")),
        "resource_version": str(metadata.get("resourceVersion", "")),
        "pod_security": required,
    }


def _node_ready(item: dict[str, Any]) -> bool:
    spec = item.get("spec")
    status = item.get("status")
    if not isinstance(spec, dict) or not isinstance(status, dict) or spec.get("unschedulable") is True:
        return False
    conditions = status.get("conditions")
    return isinstance(conditions, list) and any(
        isinstance(condition, dict)
        and condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )


def validate_fault_domains(kubectl: Kubectl) -> dict[str, Any]:
    value = kubectl.json("fault_domains", ["get", "nodes", "-o", "json"])
    items = value.get("items")
    if not isinstance(items, list):
        raise TargetError("node inventory is missing", stage="fault_domains")
    ready = [item for item in items if isinstance(item, dict) and _node_ready(item)]
    zones: dict[str, int] = {}
    hostnames: set[str] = set()
    for item in ready:
        labels = _metadata(item, label="Node").get("labels")
        if not isinstance(labels, dict):
            continue
        zone = labels.get("topology.kubernetes.io/zone")
        hostname = labels.get("kubernetes.io/hostname")
        if isinstance(zone, str) and zone and isinstance(hostname, str) and hostname:
            zones[zone] = zones.get(zone, 0) + 1
            hostnames.add(hostname)
    if len(ready) < 3 or len(hostnames) < 3 or len(zones) < 3:
        raise TargetError(
            "target requires at least three Ready schedulable nodes across three zones",
            stage="fault_domains",
        )
    return {
        "ready_schedulable_nodes": len(ready),
        "distinct_hostnames": len(hostnames),
        "zones": dict(sorted(zones.items())),
    }


def validate_metrics(kubectl: Kubectl, expected_nodes: int) -> dict[str, Any]:
    value = kubectl.json(
        "metrics_server",
        ["get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes"],
    )
    items = value.get("items")
    count = len(items) if isinstance(items, list) else 0
    if count < expected_nodes:
        raise TargetError("metrics-server does not report every Ready target node", stage="metrics_server")
    return {"reported_node_count": count}


def validate_storage_class(kubectl: Kubectl, name: str) -> dict[str, Any]:
    value = kubectl.json(
        "rwx_storage_class",
        ["get", f"storageclass.storage.k8s.io/{name}", "-o", "json"],
    )
    provisioner = value.get("provisioner")
    if not _meaningful(provisioner):
        raise TargetError("RWX StorageClass has no production provisioner", stage="rwx_storage_class")
    return {
        "name": name,
        "provisioner": provisioner,
        "volume_binding_mode": str(value.get("volumeBindingMode", "")),
        "allow_volume_expansion": value.get("allowVolumeExpansion") is True,
    }


def inspect_secret(
    kubectl: Kubectl,
    *,
    namespace: str,
    name: str,
    required_type: str,
    required_keys: set[str],
    stage: str,
) -> dict[str, Any]:
    template = (
        'go-template={{.metadata.resourceVersion}}{{"\\n"}}{{.type}}{{"\\n"}}'
        '{{range $key,$value := .data}}{{$key}}{{"\\n"}}{{end}}'
    )
    payload = kubectl.run(
        stage,
        ["get", "secret", name, "--namespace", namespace, "-o", template],
    )
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise TargetError(f"{stage} returned non-UTF-8 metadata", stage=stage) from exc
    if len(lines) < 2 or not lines[0] or lines[1] != required_type:
        raise TargetError(f"{stage} has the wrong type or no resourceVersion", stage=stage)
    keys = set(lines[2:]) - {""}
    missing = sorted(required_keys - keys)
    if missing:
        raise TargetError(f"{stage} is missing required key names: {','.join(missing)}", stage=stage)
    return {
        "name": name,
        "type": lines[1],
        "resource_version": lines[0],
        "key_names": sorted(keys),
    }


def validate_labeled_namespaces(kubectl: Kubectl, label: str, *, stage: str) -> dict[str, Any]:
    value = kubectl.json(
        stage,
        ["get", "namespaces", "--selector", label, "-o", "json"],
    )
    items = value.get("items")
    names = sorted(
        str(_metadata(item, label=stage).get("name"))
        for item in items or []
        if isinstance(item, dict)
    )
    if not names or any(not _meaningful(name) for name in names):
        raise TargetError(f"no Namespace carries required label {label}", stage=stage)
    return {"selector": label, "namespaces": names}


def deployment_permission_profile(target: dict[str, Any]) -> list[PermissionCheck]:
    namespace = str(target["namespace"])
    required = [
        PermissionCheck("ALLOW", "get", "/version"),
        PermissionCheck("ALLOW", "get", f"namespaces/{namespace}"),
        PermissionCheck("ALLOW", "list", "namespaces"),
        PermissionCheck("ALLOW", "patch", f"namespaces/{namespace}"),
        PermissionCheck("ALLOW", "get", "nodes"),
        PermissionCheck("ALLOW", "list", "nodes"),
        PermissionCheck("ALLOW", "get", "nodes.metrics.k8s.io"),
        PermissionCheck("ALLOW", "list", "nodes.metrics.k8s.io"),
        PermissionCheck(
            "ALLOW",
            "get",
            f"storageclasses.storage.k8s.io/{target['rwx_storage_class']}",
        ),
        PermissionCheck("ALLOW", "get", f"secrets/{target['runtime_secret']}", namespace),
        PermissionCheck("ALLOW", "get", f"secrets/{target['tls_secret']}", namespace),
    ]
    mutable_resources = (
        "serviceaccounts",
        "persistentvolumeclaims",
        "networkpolicies.networking.k8s.io",
        "jobs.batch",
        "deployments.apps",
        "services",
        "ingresses.networking.k8s.io",
        "poddisruptionbudgets.policy",
        "horizontalpodautoscalers.autoscaling",
    )
    for resource in mutable_resources:
        for verb in ("create", "get", "patch"):
            required.append(PermissionCheck("ALLOW", verb, resource, namespace))
    for resource in ("pods", "jobs.batch", "deployments.apps", "replicasets.apps", "persistentvolumeclaims"):
        verbs = ("get", "list", "watch") if resource in {"pods", "replicasets.apps"} else ("list", "watch")
        for verb in verbs:
            required.append(PermissionCheck("ALLOW", verb, resource, namespace))
    forbidden = [
        PermissionCheck("DENY", "*", "*", "*"),
        PermissionCheck("DENY", "create", "namespaces"),
        PermissionCheck("DENY", "delete", "namespaces"),
        PermissionCheck("DENY", "list", "secrets", namespace),
        PermissionCheck("DENY", "watch", "secrets", namespace),
        PermissionCheck("DENY", "create", "secrets", namespace),
        PermissionCheck("DENY", "update", "secrets", namespace),
        PermissionCheck("DENY", "patch", "secrets", namespace),
        PermissionCheck("DENY", "delete", "secrets", namespace),
        PermissionCheck("DENY", "delete", "deployments.apps", namespace),
        PermissionCheck("DENY", "delete", "persistentvolumeclaims", namespace),
        PermissionCheck("DENY", "create", "serviceaccounts/token", namespace),
        PermissionCheck("DENY", "impersonate", "users"),
        PermissionCheck("DENY", "impersonate", "groups"),
        PermissionCheck("DENY", "impersonate", "serviceaccounts"),
    ]
    for resource in (
        "roles.rbac.authorization.k8s.io",
        "rolebindings.rbac.authorization.k8s.io",
        "clusterroles.rbac.authorization.k8s.io",
        "clusterrolebindings.rbac.authorization.k8s.io",
    ):
        for verb in ("create", "update", "patch", "delete"):
            forbidden.append(PermissionCheck("DENY", verb, resource, namespace if not resource.startswith("cluster") else None))
    profile = [*required, *forbidden]
    unique = {(item.expectation, item.verb, item.resource, item.namespace) for item in profile}
    if len(unique) != len(profile):
        raise TargetError("deployment permission profile contains duplicate checks")
    return profile


def validate_permissions(kubectl: Kubectl, target: dict[str, Any]) -> dict[str, Any]:
    profile = deployment_permission_profile(target)
    for index, check in enumerate(profile, start=1):
        arguments = ["auth", "can-i", check.verb, check.resource]
        if check.namespace == "*":
            arguments.append("--all-namespaces")
        elif check.namespace:
            arguments.extend(["--namespace", check.namespace])
        output = kubectl.run(f"rbac_{index:03d}", arguments).decode("utf-8", errors="replace").strip().lower()
        observed = output == "yes"
        if output not in {"yes", "no"} or observed != (check.expectation == "ALLOW"):
            raise TargetError(
                f"deployment identity violates RBAC profile at {check.verb} {check.resource}",
                stage=f"rbac_{index:03d}",
            )
    return {
        "check_count": len(profile),
        "allowed_count": sum(item.expectation == "ALLOW" for item in profile),
        "denied_count": sum(item.expectation == "DENY" for item in profile),
    }


def server_dry_run(kubectl: Kubectl, bundle_dir: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for phase in ("bootstrap.yaml", "migration.yaml", "applications.yaml"):
        path = bundle_dir / phase
        kubectl.run(
            f"server_dry_run_{phase.removesuffix('.yaml')}",
            [
                "apply",
                "--server-side",
                "--dry-run=server",
                "--field-manager=duckdock-ga-preflight",
                "-f",
                str(path),
            ],
            timeout_seconds=180,
        )
        digests[phase] = sha256_file(path)
    return digests


def preflight_command_stages(target: dict[str, Any]) -> list[str]:
    return [
        "cluster_identity_principal",
        "cluster_identity_uid",
        "kubernetes_version",
        "target_namespace",
        "fault_domains",
        "metrics_server",
        "rwx_storage_class",
        "runtime_secret_metadata",
        "tls_secret_metadata",
        "ingress_namespace_labels",
        "monitoring_namespace_labels",
        *[
            f"rbac_{index:03d}"
            for index in range(1, len(deployment_permission_profile(target)) + 1)
        ],
        "server_dry_run_bootstrap",
        "server_dry_run_migration",
        "server_dry_run_applications",
    ]


def deployment_command_stages(target: dict[str, Any]) -> list[str]:
    return [
        *preflight_command_stages(target),
        "apply_bootstrap",
        "wait_rwx_bound",
        "apply_migration",
        "wait_migration",
        "migration_result",
        "apply_applications",
        *[
            stage
            for name in ALL_DEPLOYMENTS
            for stage in (f"rollout_{name}", f"rollout_result_{name}")
        ],
    ]


def validate_command_records(
    value: Any,
    *,
    context: str,
    expected_stages: list[str],
    require_success: bool,
    allow_prefix: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TargetError("command evidence must be a list")
    observed_stages: list[str] = []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "stage",
            "argv",
            "exit_code",
            "stdout_sha256",
            "stderr_sha256",
            "started_at",
            "finished_at",
            "duration_ms",
        }:
            raise TargetError("command evidence has missing or unexpected fields")
        stage = item.get("stage")
        argv = item.get("argv")
        exit_code = item.get("exit_code")
        duration = item.get("duration_ms")
        if (
            not isinstance(stage, str)
            or not isinstance(argv, list)
            or argv[:3] != ["kubectl", "--context", context]
            or not all(isinstance(argument, str) and argument.isprintable() for argument in argv)
            or not isinstance(exit_code, int)
            or not isinstance(duration, int)
            or duration < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("stdout_sha256", ""))) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("stderr_sha256", ""))) is None
        ):
            raise TargetError("command evidence is malformed")
        try:
            started = datetime.fromisoformat(str(item["started_at"]))
            finished = datetime.fromisoformat(str(item["finished_at"]))
        except ValueError as exc:
            raise TargetError("command evidence timestamp is invalid") from exc
        if started.tzinfo is None or finished.tzinfo is None or finished < started:
            raise TargetError("command evidence timestamp ordering is invalid")
        observed_stages.append(stage)
        result.append(dict(item))
    if allow_prefix:
        if observed_stages != expected_stages[: len(observed_stages)]:
            raise TargetError("command stages are not an exact deployment prefix")
    elif observed_stages != expected_stages:
        raise TargetError("command stages are incomplete or reordered")
    if require_success and any(item["exit_code"] != 0 for item in result):
        raise TargetError("successful command evidence contains a failure")
    if not require_success and any(item["exit_code"] != 0 for item in result[:-1]):
        raise TargetError("deployment command failed before its recorded terminal stage")
    return result


def validate_preflight_kubernetes(
    value: Any,
    *,
    target: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "identity",
        "version",
        "namespace",
        "fault_domains",
        "metrics_server",
        "storage_class",
        "runtime_secret",
        "tls_secret",
        "ingress_namespaces",
        "monitoring_namespaces",
        "deployment_permissions",
    }:
        raise TargetError("preflight Kubernetes projection has invalid fields")
    identity = value["identity"]
    if (
        not isinstance(identity, dict)
        or set(identity) != {"context", "namespace", "cluster_uid", "principal"}
        or identity.get("namespace") != target["namespace"]
        or not all(_meaningful(identity.get(key)) for key in ("context", "cluster_uid", "principal"))
    ):
        raise TargetError("preflight Kubernetes identity is invalid")
    version = value["version"]
    if (
        not isinstance(version, dict)
        or set(version) != {"git_version", "major", "minor"}
        or not isinstance(version.get("major"), int)
        or not isinstance(version.get("minor"), int)
        or (version["major"], version["minor"]) < MINIMUM_KUBERNETES
    ):
        raise TargetError("preflight Kubernetes version is invalid")
    namespace = value["namespace"]
    if (
        not isinstance(namespace, dict)
        or namespace.get("name") != target["namespace"]
        or not _meaningful(namespace.get("uid"))
        or not _meaningful(namespace.get("resource_version"))
        or namespace.get("pod_security")
        != {
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/audit": "restricted",
            "pod-security.kubernetes.io/warn": "restricted",
        }
    ):
        raise TargetError("preflight target Namespace projection is invalid")
    fault_domains = value["fault_domains"]
    zones = fault_domains.get("zones") if isinstance(fault_domains, dict) else None
    if (
        not isinstance(fault_domains, dict)
        or set(fault_domains) != {"ready_schedulable_nodes", "distinct_hostnames", "zones"}
        or not isinstance(zones, dict)
        or len(zones) < 3
        or any(not _meaningful(name) or not isinstance(count, int) or count < 1 for name, count in zones.items())
        or int(fault_domains.get("ready_schedulable_nodes", 0)) < 3
        or int(fault_domains.get("distinct_hostnames", 0)) < 3
    ):
        raise TargetError("preflight fault-domain projection is invalid")
    metrics = value["metrics_server"]
    if (
        not isinstance(metrics, dict)
        or set(metrics) != {"reported_node_count"}
        or int(metrics.get("reported_node_count", 0))
        < int(fault_domains["ready_schedulable_nodes"])
    ):
        raise TargetError("preflight metrics-server projection is invalid")
    storage = value["storage_class"]
    if (
        not isinstance(storage, dict)
        or set(storage)
        != {"name", "provisioner", "volume_binding_mode", "allow_volume_expansion"}
        or storage.get("name") != target["rwx_storage_class"]
        or not _meaningful(storage.get("provisioner"))
        or not isinstance(storage.get("allow_volume_expansion"), bool)
    ):
        raise TargetError("preflight RWX StorageClass projection is invalid")
    for key, expected_name, expected_type, required_keys in (
        ("runtime_secret", target["runtime_secret"], "Opaque", REQUIRED_RUNTIME_SECRET_KEYS),
        ("tls_secret", target["tls_secret"], "kubernetes.io/tls", REQUIRED_TLS_SECRET_KEYS),
    ):
        secret = value[key]
        if (
            not isinstance(secret, dict)
            or set(secret) != {"name", "type", "resource_version", "key_names"}
            or secret.get("name") != expected_name
            or secret.get("type") != expected_type
            or not _meaningful(secret.get("resource_version"))
            or not isinstance(secret.get("key_names"), list)
            or not required_keys.issubset(set(secret["key_names"]))
            or secret["key_names"] != sorted(set(secret["key_names"]))
        ):
            raise TargetError(f"preflight {key} projection is invalid")
    for key, selector in (
        ("ingress_namespaces", "duckdock.io/ingress=true"),
        ("monitoring_namespaces", "duckdock.io/monitoring=true"),
    ):
        projection = value[key]
        if (
            not isinstance(projection, dict)
            or set(projection) != {"selector", "namespaces"}
            or projection.get("selector") != selector
            or not isinstance(projection.get("namespaces"), list)
            or not projection["namespaces"]
            or projection["namespaces"] != sorted(set(projection["namespaces"]))
            or any(not _meaningful(name) for name in projection["namespaces"])
        ):
            raise TargetError(f"preflight {key} projection is invalid")
    permissions = value["deployment_permissions"]
    profile = deployment_permission_profile(target)
    allowed = sum(item.expectation == "ALLOW" for item in profile)
    if permissions != {
        "check_count": len(profile),
        "allowed_count": allowed,
        "denied_count": len(profile) - allowed,
    }:
        raise TargetError("preflight deployment permission projection is invalid")
    return json.loads(json.dumps(value, sort_keys=True))


def collect_preflight(
    bundle_dir: Path,
    *,
    context: str,
    expected_cluster_uid: str,
    expected_principal: str,
    runner: Runner = default_runner,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(timezone.utc)
    bundle_dir = bundle_dir.expanduser().resolve(strict=True)
    bundle = _bundle_api().verify_bundle(bundle_dir)
    target = bundle["target"]
    context = validate_context_value(context, field="Kubernetes context")
    expected_cluster_uid = validate_context_value(expected_cluster_uid, field="cluster UID")
    expected_principal = validate_context_value(expected_principal, field="Kubernetes principal")
    kubectl = Kubectl(context, runner)
    identity = observe_identity(
        kubectl,
        namespace=target["namespace"],
        expected_cluster_uid=expected_cluster_uid,
        expected_principal=expected_principal,
    )
    version = kubernetes_version(kubectl)
    namespace = validate_namespace(kubectl, target["namespace"])
    fault_domains = validate_fault_domains(kubectl)
    metrics = validate_metrics(kubectl, fault_domains["ready_schedulable_nodes"])
    storage = validate_storage_class(kubectl, target["rwx_storage_class"])
    runtime_secret = inspect_secret(
        kubectl,
        namespace=target["namespace"],
        name=target["runtime_secret"],
        required_type="Opaque",
        required_keys=REQUIRED_RUNTIME_SECRET_KEYS,
        stage="runtime_secret_metadata",
    )
    tls_secret = inspect_secret(
        kubectl,
        namespace=target["namespace"],
        name=target["tls_secret"],
        required_type="kubernetes.io/tls",
        required_keys=REQUIRED_TLS_SECRET_KEYS,
        stage="tls_secret_metadata",
    )
    ingress_namespaces = validate_labeled_namespaces(
        kubectl,
        "duckdock.io/ingress=true",
        stage="ingress_namespace_labels",
    )
    monitoring_namespaces = validate_labeled_namespaces(
        kubectl,
        "duckdock.io/monitoring=true",
        stage="monitoring_namespace_labels",
    )
    permissions = validate_permissions(kubectl, target)
    manifest_digests = server_dry_run(kubectl, bundle_dir)
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": PREFLIGHT_STATUS,
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "observed_at": current.isoformat(),
        "bundle": {
            "directory": str(bundle_dir),
            "receipt_sha256": bundle["receipt_sha256"],
            "source": bundle["source"],
            "target": target,
            "migration_job": bundle["migration_job"],
            "verification": bundle["verification"],
            "manifest_sha256": manifest_digests,
        },
        "kubernetes": {
            "identity": identity,
            "version": version,
            "namespace": namespace,
            "fault_domains": fault_domains,
            "metrics_server": metrics,
            "storage_class": storage,
            "runtime_secret": runtime_secret,
            "tls_secret": tls_secret,
            "ingress_namespaces": ingress_namespaces,
            "monitoring_namespaces": monitoring_namespaces,
            "deployment_permissions": permissions,
        },
        "validated_requirements": PREFLIGHT_VALIDATED_REQUIREMENTS,
        "remaining_external_requirements": PENDING_AFTER_DEPLOYMENT,
        "commands": kubectl.commands,
    }


def verify_preflight(
    path: Path,
    *,
    bundle_dir: Path | None = None,
    now: datetime | None = None,
    require_fresh: bool = False,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(timezone.utc)
    receipt = load_receipt(path, expected_schema=PREFLIGHT_SCHEMA_VERSION)
    if set(receipt) != {
        "schema_version",
        "status",
        "authorization_boundary",
        "observed_at",
        "bundle",
        "kubernetes",
        "validated_requirements",
        "remaining_external_requirements",
        "commands",
    }:
        raise TargetError("preflight receipt has missing or unexpected fields")
    if receipt.get("status") != PREFLIGHT_STATUS:
        raise TargetError("preflight receipt status is invalid")
    try:
        observed_at = datetime.fromisoformat(str(receipt["observed_at"]))
    except (KeyError, ValueError) as exc:
        raise TargetError("preflight observed_at is invalid") from exc
    if observed_at.tzinfo is None:
        raise TargetError("preflight observed_at must include a timezone")
    observed_at = observed_at.astimezone(timezone.utc)
    if observed_at > current + timedelta(minutes=5):
        raise TargetError("preflight receipt is future-dated")
    if require_fresh and current - observed_at > PREFLIGHT_MAX_AGE:
        raise TargetError("preflight receipt is stale for target mutation")
    bundle = receipt.get("bundle")
    if not isinstance(bundle, dict) or set(bundle) != {
        "directory",
        "receipt_sha256",
        "source",
        "target",
        "migration_job",
        "verification",
        "manifest_sha256",
    }:
        raise TargetError("preflight receipt has no bundle binding")
    recorded_dir = Path(str(bundle.get("directory", ""))).resolve(strict=True)
    if bundle_dir is not None and recorded_dir != bundle_dir.expanduser().resolve(strict=True):
        raise TargetError("preflight receipt is bound to another bundle directory")
    verified_bundle = _bundle_api().verify_bundle(recorded_dir)
    if (
        bundle.get("receipt_sha256") != verified_bundle["receipt_sha256"]
        or bundle.get("source") != verified_bundle["source"]
        or bundle.get("target") != verified_bundle["target"]
        or bundle.get("migration_job") != verified_bundle["migration_job"]
        or bundle.get("verification") != verified_bundle["verification"]
    ):
        raise TargetError("preflight bundle binding changed")
    manifests = bundle.get("manifest_sha256")
    expected_manifests = {
        name: sha256_file(recorded_dir / name)
        for name in ("bootstrap.yaml", "migration.yaml", "applications.yaml")
    }
    if manifests != expected_manifests:
        raise TargetError("preflight manifest digests changed")
    validated = receipt.get("validated_requirements")
    if validated != PREFLIGHT_VALIDATED_REQUIREMENTS:
        raise TargetError("preflight validated requirements changed")
    if receipt.get("remaining_external_requirements") != PENDING_AFTER_DEPLOYMENT:
        raise TargetError("preflight remaining external requirements changed")
    kubernetes = validate_preflight_kubernetes(
        receipt.get("kubernetes"),
        target=verified_bundle["target"],
    )
    validate_command_records(
        receipt.get("commands"),
        context=kubernetes["identity"]["context"],
        expected_stages=preflight_command_stages(verified_bundle["target"]),
        require_success=True,
    )
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": "TARGET_HA_PREFLIGHT_VERIFIED_NOT_GA_AUTHORIZED",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "receipt_sha256": sha256_file(path.resolve()),
        "bundle": bundle,
        "kubernetes": kubernetes,
        "validated_requirements": validated,
        "remaining_external_requirements": PENDING_AFTER_DEPLOYMENT,
        "observed_at": observed_at.isoformat(),
        "verified_at": current.isoformat(),
    }


def _preflight_projection(receipt: dict[str, Any]) -> dict[str, Any]:
    kubernetes = receipt["kubernetes"]
    return {
        "bundle": receipt["bundle"],
        "identity": kubernetes["identity"],
        "version": kubernetes["version"],
        "namespace": kubernetes["namespace"],
        "fault_domains": kubernetes["fault_domains"],
        "metrics_server": kubernetes["metrics_server"],
        "storage_class": kubernetes["storage_class"],
        "runtime_secret": kubernetes["runtime_secret"],
        "tls_secret": kubernetes["tls_secret"],
        "ingress_namespaces": kubernetes["ingress_namespaces"],
        "monitoring_namespaces": kubernetes["monitoring_namespaces"],
        "deployment_permissions": kubernetes["deployment_permissions"],
    }


def mutation_confirmation(context: str, namespace: str, bundle_digest: str) -> str:
    return f"APPLY_DUCKDOCK_HA_TARGET:{context}:{namespace}:{bundle_digest}"


def _validate_job(job: dict[str, Any], *, bundle: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata(job, label="migration Job")
    spec = job.get("spec")
    status = job.get("status")
    if not isinstance(spec, dict) or not isinstance(status, dict):
        raise TargetError("migration Job lacks spec/status", stage="migration_result")
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    complete = any(
        isinstance(item, dict) and item.get("type") == "Complete" and item.get("status") == "True"
        for item in status.get("conditions") or []
    )
    expected_image = bundle["target"]["backend_image"]
    if (
        metadata.get("name") != bundle["migration_job"]
        or metadata.get("annotations", {}).get("duckdock.io/release-commit")
        != bundle["source"]["commit"]
        or len(containers) != 1
        or containers[0].get("image") != expected_image
        or not complete
        or int(status.get("succeeded", 0)) < 1
        or int(status.get("failed", 0)) != 0
    ):
        raise TargetError("migration Job did not complete with the approved release", stage="migration_result")
    return {
        "name": metadata["name"],
        "uid": str(metadata.get("uid", "")),
        "succeeded": int(status.get("succeeded", 0)),
        "failed": int(status.get("failed", 0)),
        "image": expected_image,
    }


def _validate_deployment(
    deployment: dict[str, Any],
    *,
    name: str,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    metadata = _metadata(deployment, label=f"Deployment/{name}")
    spec = deployment.get("spec")
    status = deployment.get("status")
    if not isinstance(spec, dict) or not isinstance(status, dict):
        raise TargetError(f"Deployment/{name} lacks spec/status", stage=f"rollout_{name}")
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    replicas = int(spec.get("replicas", 0))
    minimum = 3 if name in REPLICATED_DEPLOYMENTS else 1
    expected_image = (
        bundle["target"]["frontend_image"]
        if name == "frontend"
        else bundle["target"]["backend_image"]
    )
    generation = int(metadata.get("generation", 0))
    if (
        metadata.get("name") != name
        or metadata.get("annotations", {}).get("duckdock.io/release-commit")
        != bundle["source"]["commit"]
        or len(containers) != 1
        or containers[0].get("image") != expected_image
        or replicas < minimum
        or int(status.get("observedGeneration", 0)) != generation
        or int(status.get("updatedReplicas", 0)) != replicas
        or int(status.get("availableReplicas", 0)) < replicas
        or int(status.get("unavailableReplicas", 0)) != 0
    ):
        raise TargetError(f"Deployment/{name} is not fully rolled out", stage=f"rollout_{name}")
    return {
        "name": name,
        "uid": str(metadata.get("uid", "")),
        "generation": generation,
        "replicas": replicas,
        "available_replicas": int(status.get("availableReplicas", 0)),
        "image": expected_image,
    }


def execute_deployment(
    *,
    bundle_dir: Path,
    preflight_receipt: Path,
    context: str,
    expected_cluster_uid: str,
    expected_principal: str,
    change_request_id: str,
    confirmation: str,
    output: Path,
    runner: Runner = default_runner,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(timezone.utc)
    bundle_dir = bundle_dir.expanduser().resolve(strict=True)
    output = validate_output(output, bundle_dir=bundle_dir)
    if SAFE_ID_RE.fullmatch(change_request_id) is None:
        raise TargetError("change request ID must be 8-128 safe characters")
    verified_preflight = verify_preflight(
        preflight_receipt,
        bundle_dir=bundle_dir,
        now=current,
        require_fresh=True,
    )
    bundle = verified_preflight["bundle"]
    expected_confirmation = mutation_confirmation(
        context,
        bundle["target"]["namespace"],
        bundle["receipt_sha256"],
    )
    if confirmation != expected_confirmation:
        raise TargetError("target mutation confirmation does not exactly match the bound target")

    completed_phases: list[str] = []
    mutation_started = False
    kubectl: Kubectl | None = None
    live_preflight: dict[str, Any] | None = None
    migration: dict[str, Any] | None = None
    deployments: list[dict[str, Any]] = []
    try:
        live_preflight = collect_preflight(
            bundle_dir,
            context=context,
            expected_cluster_uid=expected_cluster_uid,
            expected_principal=expected_principal,
            runner=runner,
            now=current,
        )
        original = load_receipt(preflight_receipt, expected_schema=PREFLIGHT_SCHEMA_VERSION)
        if _preflight_projection(live_preflight) != _preflight_projection(original):
            raise TargetError("target preflight state drifted before deployment", stage="preflight_drift")
        kubectl = Kubectl(context, runner)
        namespace = bundle["target"]["namespace"]
        mutation_started = True
        kubectl.run(
            "apply_bootstrap",
            [
                "apply",
                "--server-side",
                "--field-manager=duckdock-ga-deployer",
                "-f",
                str(bundle_dir / "bootstrap.yaml"),
            ],
            timeout_seconds=180,
        )
        kubectl.run(
            "wait_rwx_bound",
            [
                "wait",
                "--namespace",
                namespace,
                "--for=jsonpath={.status.phase}=Bound",
                f"persistentvolumeclaim/{bundle['target']['rwx_claim']}",
                "--timeout=10m",
            ],
            timeout_seconds=660,
        )
        completed_phases.append("bootstrap")
        kubectl.run(
            "apply_migration",
            [
                "apply",
                "--server-side",
                "--field-manager=duckdock-ga-deployer",
                "-f",
                str(bundle_dir / "migration.yaml"),
            ],
            timeout_seconds=180,
        )
        kubectl.run(
            "wait_migration",
            [
                "wait",
                "--namespace",
                namespace,
                "--for=condition=complete",
                f"job/{bundle['migration_job']}",
                "--timeout=10m",
            ],
            timeout_seconds=660,
        )
        job = kubectl.json(
            "migration_result",
            ["get", "--namespace", namespace, f"job/{bundle['migration_job']}", "-o", "json"],
        )
        migration = _validate_job(job, bundle=bundle)
        completed_phases.append("migration")
        kubectl.run(
            "apply_applications",
            [
                "apply",
                "--server-side",
                "--field-manager=duckdock-ga-deployer",
                "-f",
                str(bundle_dir / "applications.yaml"),
            ],
            timeout_seconds=180,
        )
        for name in ALL_DEPLOYMENTS:
            kubectl.run(
                f"rollout_{name}",
                [
                    "rollout",
                    "status",
                    "--namespace",
                    namespace,
                    f"deployment/{name}",
                    "--timeout=10m",
                ],
                timeout_seconds=660,
            )
            value = kubectl.json(
                f"rollout_result_{name}",
                ["get", "--namespace", namespace, f"deployment/{name}", "-o", "json"],
            )
            deployments.append(_validate_deployment(value, name=name, bundle=bundle))
        completed_phases.append("applications")
        receipt = {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "status": DEPLOYED_STATUS,
            "authorization_boundary": AUTHORIZATION_BOUNDARY,
            "change_request_id": change_request_id,
            "observed_at": current.isoformat(),
            "completed_at": iso_now(),
            "bundle": bundle,
            "preflight_receipt": {
                "path": str(preflight_receipt.resolve()),
                "sha256": sha256_file(preflight_receipt.resolve()),
            },
            "kubernetes": live_preflight["kubernetes"],
            "completed_phases": completed_phases,
            "migration": migration,
            "deployments": deployments,
            "remaining_external_requirements": PENDING_AFTER_DEPLOYMENT,
            "commands": [*live_preflight["commands"], *kubectl.commands],
        }
        digest = write_receipt(output, receipt)
        return {**receipt, "receipt_path": str(output), "receipt_sha256": digest}
    except Exception as exc:
        stage = exc.stage if isinstance(exc, TargetError) else type(exc).__name__
        status = INCOMPLETE_STATUS if mutation_started else REFUSED_STATUS
        receipt = {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "status": status,
            "authorization_boundary": AUTHORIZATION_BOUNDARY,
            "change_request_id": change_request_id,
            "observed_at": current.isoformat(),
            "failed_at": iso_now(),
            "failure": {"stage": stage, "category": type(exc).__name__},
            "bundle": bundle,
            "preflight_receipt": {
                "path": str(preflight_receipt.resolve()),
                "sha256": sha256_file(preflight_receipt.resolve()),
            },
            "completed_phases": completed_phases,
            "migration": migration,
            "deployments": deployments,
            "remaining_external_requirements": PENDING_AFTER_DEPLOYMENT,
            "commands": [
                *(live_preflight.get("commands", []) if isinstance(live_preflight, dict) else []),
                *(kubectl.commands if kubectl is not None else []),
            ],
        }
        digest = write_receipt(output, receipt)
        raise TargetError(
            f"deployment stopped at {stage}; immutable receipt {output} ({digest})",
            stage=stage,
        ) from exc


def verify_deployment(path: Path) -> dict[str, Any]:
    receipt = load_receipt(path, expected_schema=DEPLOYMENT_SCHEMA_VERSION)
    status = receipt.get("status")
    if status not in {DEPLOYED_STATUS, INCOMPLETE_STATUS, REFUSED_STATUS}:
        raise TargetError("deployment receipt status is invalid")
    common_keys = {
        "schema_version",
        "status",
        "authorization_boundary",
        "change_request_id",
        "observed_at",
        "bundle",
        "preflight_receipt",
        "completed_phases",
        "migration",
        "deployments",
        "remaining_external_requirements",
        "commands",
    }
    expected_keys = (
        common_keys | {"completed_at", "kubernetes"}
        if status == DEPLOYED_STATUS
        else common_keys | {"failed_at", "failure"}
    )
    if set(receipt) != expected_keys:
        raise TargetError("deployment receipt has missing or unexpected fields")
    change_request = receipt.get("change_request_id")
    if not isinstance(change_request, str) or SAFE_ID_RE.fullmatch(change_request) is None:
        raise TargetError("deployment receipt change request ID is invalid")
    for field in ("observed_at", "completed_at" if status == DEPLOYED_STATUS else "failed_at"):
        try:
            timestamp = datetime.fromisoformat(str(receipt[field]))
        except ValueError as exc:
            raise TargetError(f"deployment receipt {field} is invalid") from exc
        if timestamp.tzinfo is None:
            raise TargetError(f"deployment receipt {field} lacks a timezone")
    bundle = receipt.get("bundle")
    if not isinstance(bundle, dict) or set(bundle) != {
        "directory",
        "receipt_sha256",
        "source",
        "target",
        "migration_job",
        "verification",
        "manifest_sha256",
    }:
        raise TargetError("deployment receipt has no bundle binding")
    bundle_dir = Path(str(bundle.get("directory", ""))).resolve(strict=True)
    verified_bundle = _bundle_api().verify_bundle(bundle_dir)
    if any(
        bundle.get(key) != verified_bundle.get(key)
        for key in ("receipt_sha256", "source", "target", "migration_job", "verification")
    ):
        raise TargetError("deployment receipt bundle binding changed")
    preflight_reference = receipt.get("preflight_receipt")
    if not isinstance(preflight_reference, dict) or set(preflight_reference) != {
        "path",
        "sha256",
    }:
        raise TargetError("deployment receipt has no preflight reference")
    preflight_path = Path(str(preflight_reference.get("path", ""))).resolve(strict=True)
    if sha256_file(preflight_path) != preflight_reference.get("sha256"):
        raise TargetError("deployment preflight reference changed")
    verify_preflight(preflight_path, bundle_dir=bundle_dir)
    completed = receipt.get("completed_phases")
    allowed_prefixes = [[], ["bootstrap"], ["bootstrap", "migration"], ["bootstrap", "migration", "applications"]]
    if completed not in allowed_prefixes:
        raise TargetError("deployment phase projection is not a valid prefix")
    if status == DEPLOYED_STATUS:
        if completed != allowed_prefixes[-1]:
            raise TargetError("completed deployment receipt omitted a phase")
        migration = receipt.get("migration")
        deployments = receipt.get("deployments")
        if (
            not isinstance(migration, dict)
            or set(migration) != {"name", "uid", "succeeded", "failed", "image"}
            or migration.get("name") != bundle["migration_job"]
            or not _meaningful(migration.get("uid"))
            or migration.get("succeeded") != 1
            or migration.get("failed") != 0
            or migration.get("image") != bundle["target"]["backend_image"]
            or not isinstance(deployments, list)
            or {item.get("name") for item in deployments if isinstance(item, dict)}
            != set(ALL_DEPLOYMENTS)
        ):
            raise TargetError("completed deployment receipt lacks workload results")
        for item in deployments:
            if not isinstance(item, dict) or set(item) != {
                "name",
                "uid",
                "generation",
                "replicas",
                "available_replicas",
                "image",
            }:
                raise TargetError("deployment workload result is malformed")
            name = str(item["name"])
            minimum = 3 if name in REPLICATED_DEPLOYMENTS else 1
            expected_image = (
                bundle["target"]["frontend_image"]
                if name == "frontend"
                else bundle["target"]["backend_image"]
            )
            if (
                not _meaningful(item.get("uid"))
                or not isinstance(item.get("generation"), int)
                or item["generation"] < 1
                or not isinstance(item.get("replicas"), int)
                or item["replicas"] < minimum
                or item.get("available_replicas") != item["replicas"]
                or item.get("image") != expected_image
            ):
                raise TargetError("deployment workload result does not match the approved release")
        kubernetes = validate_preflight_kubernetes(
            receipt.get("kubernetes"),
            target=bundle["target"],
        )
        validate_command_records(
            receipt.get("commands"),
            context=kubernetes["identity"]["context"],
            expected_stages=deployment_command_stages(bundle["target"]),
            require_success=True,
        )
    else:
        failure = receipt.get("failure")
        migration = receipt.get("migration")
        deployments = receipt.get("deployments")
        if (
            not isinstance(failure, dict)
            or set(failure) != {"stage", "category"}
            or not _meaningful(failure.get("stage"))
            or not _meaningful(failure.get("category"))
            or not isinstance(deployments, list)
        ):
            raise TargetError("incomplete deployment failure projection is invalid")
        if "migration" in completed:
            if (
                not isinstance(migration, dict)
                or set(migration) != {"name", "uid", "succeeded", "failed", "image"}
                or migration.get("name") != bundle["migration_job"]
                or not _meaningful(migration.get("uid"))
                or migration.get("succeeded") != 1
                or migration.get("failed") != 0
                or migration.get("image") != bundle["target"]["backend_image"]
            ):
                raise TargetError("incomplete deployment migration projection is invalid")
        elif migration is not None:
            raise TargetError("incomplete deployment records an uncompleted migration")
        if len(deployments) > len(ALL_DEPLOYMENTS):
            raise TargetError("incomplete deployment records too many workloads")
        observed_names: list[str] = []
        for item in deployments:
            if not isinstance(item, dict) or set(item) != {
                "name",
                "uid",
                "generation",
                "replicas",
                "available_replicas",
                "image",
            }:
                raise TargetError("incomplete deployment workload result is malformed")
            name = str(item["name"])
            observed_names.append(name)
            minimum = 3 if name in REPLICATED_DEPLOYMENTS else 1
            expected_image = (
                bundle["target"]["frontend_image"]
                if name == "frontend"
                else bundle["target"]["backend_image"]
            )
            if (
                not _meaningful(item.get("uid"))
                or not isinstance(item.get("generation"), int)
                or item["generation"] < 1
                or not isinstance(item.get("replicas"), int)
                or item["replicas"] < minimum
                or item.get("available_replicas") != item["replicas"]
                or item.get("image") != expected_image
            ):
                raise TargetError("incomplete deployment workload result is invalid")
        if observed_names != list(ALL_DEPLOYMENTS[: len(observed_names)]):
            raise TargetError("incomplete deployment workloads are not an exact rollout prefix")
        if status == REFUSED_STATUS and (completed or migration is not None or deployments):
            raise TargetError("refused deployment receipt records target mutation results")
        commands = receipt.get("commands")
        if commands:
            context = str(bundle["target"].get("namespace", ""))
            first = commands[0] if isinstance(commands, list) else None
            if isinstance(first, dict) and isinstance(first.get("argv"), list) and len(first["argv"]) >= 3:
                context = str(first["argv"][2])
            validate_command_records(
                commands,
                context=context,
                expected_stages=deployment_command_stages(bundle["target"]),
                require_success=False,
                allow_prefix=True,
            )
    if receipt.get("remaining_external_requirements") != PENDING_AFTER_DEPLOYMENT:
        raise TargetError("deployment remaining external requirements changed")
    return {
        "schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "status": f"{status}_VERIFIED",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "receipt_sha256": sha256_file(path.resolve()),
        "bundle_receipt_sha256": bundle["receipt_sha256"],
        "completed_phases": completed,
        "remaining_external_requirements": PENDING_AFTER_DEPLOYMENT,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Run target I/O and server-side dry-run without persistence")
    preflight.add_argument("--bundle-dir", type=Path, required=True)
    preflight.add_argument("--context", required=True)
    preflight.add_argument("--expected-cluster-uid", required=True)
    preflight.add_argument("--expected-principal", required=True)
    preflight.add_argument("--output", type=Path, required=True)

    deploy = subparsers.add_parser("deploy", help="Apply the three target phases after a fresh preflight")
    deploy.add_argument("--bundle-dir", type=Path, required=True)
    deploy.add_argument("--preflight-receipt", type=Path, required=True)
    deploy.add_argument("--context", required=True)
    deploy.add_argument("--expected-cluster-uid", required=True)
    deploy.add_argument("--expected-principal", required=True)
    deploy.add_argument("--change-request-id", required=True)
    deploy.add_argument("--confirm-target-mutation", required=True)
    deploy.add_argument("--output", type=Path, required=True)

    verify_preflight_parser = subparsers.add_parser("verify-preflight", help="Verify a preflight receipt offline")
    verify_preflight_parser.add_argument("--receipt", type=Path, required=True)
    verify_preflight_parser.add_argument("--bundle-dir", type=Path)

    verify_deployment_parser = subparsers.add_parser("verify-deployment", help="Verify a deployment receipt offline")
    verify_deployment_parser.add_argument("--receipt", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "preflight":
            bundle_dir = args.bundle_dir.expanduser().resolve(strict=True)
            output = validate_output(args.output, bundle_dir=bundle_dir)
            receipt = collect_preflight(
                bundle_dir,
                context=args.context,
                expected_cluster_uid=args.expected_cluster_uid,
                expected_principal=args.expected_principal,
            )
            digest = write_receipt(output, receipt)
            result = {**receipt, "receipt_path": str(output), "receipt_sha256": digest}
        elif args.command == "deploy":
            result = execute_deployment(
                bundle_dir=args.bundle_dir,
                preflight_receipt=args.preflight_receipt,
                context=args.context,
                expected_cluster_uid=args.expected_cluster_uid,
                expected_principal=args.expected_principal,
                change_request_id=args.change_request_id,
                confirmation=args.confirm_target_mutation,
                output=args.output,
            )
        elif args.command == "verify-preflight":
            result = verify_preflight(args.receipt, bundle_dir=args.bundle_dir)
        else:
            result = verify_deployment(args.receipt)
    except (TargetError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"HA target deployment refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
