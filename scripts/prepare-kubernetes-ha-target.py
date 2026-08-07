#!/usr/bin/env python3
"""Prepare or verify a fail-closed DuckDock target Kubernetes HA bundle."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HA_BASE = REPO_ROOT / "ops" / "kubernetes" / "ha"
SCHEMA_VERSION = "duckdock-kubernetes-ha-target-bundle-v1"
AUTHORIZATION_SCOPE = (
    "prepared target deployment manifests only; server-side dry-run, live rollout, "
    "fault injection, state-service verification and GA authorization remain mandatory"
)
IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9.-]*(?::[0-9]+)?"
    r"(?:/[a-z0-9][a-z0-9._-]*)+@sha256:[0-9a-f]{64}$"
)
DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
K8S_NAME_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
SIZE_RE = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>Gi|Ti)$")
EXPECTED_KIND_COUNTS = {
    "Namespace": 1,
    "ServiceAccount": 1,
    "PersistentVolumeClaim": 1,
    "Job": 1,
    "Deployment": 4,
    "Service": 2,
    "Ingress": 1,
    "PodDisruptionBudget": 3,
    "HorizontalPodAutoscaler": 3,
    "NetworkPolicy": 5,
}
PHASE_FILES = {
    "bootstrap.yaml": {
        "Namespace",
        "ServiceAccount",
        "PersistentVolumeClaim",
        "NetworkPolicy",
    },
    "migration.yaml": {"Job"},
    "applications.yaml": {
        "Deployment",
        "Service",
        "Ingress",
        "PodDisruptionBudget",
        "HorizontalPodAutoscaler",
    },
}
REPLICATED_COMPONENTS = {"backend", "frontend", "worker"}
STATEFUL_BLOCKED_KINDS = {"StatefulSet", "Secret"}
EXPECTED_DEPLOYMENTS = {"backend", "frontend", "worker", "beat"}
EXPECTED_SERVICES = {"backend", "frontend"}
EXPECTED_POLICIES = {
    "default-deny",
    "allow-dns",
    "frontend-ingress-and-backend",
    "backend-from-frontend",
    "controlled-external-egress",
}
EXTERNAL_REQUIREMENTS = {
    "release_image_provenance": "PENDING_EXTERNAL",
    "runtime_secret_values": "PENDING_EXTERNAL",
    "tls_certificate": "PENDING_EXTERNAL",
    "rwx_storage_redundancy": "PENDING_EXTERNAL",
    "managed_mysql_redis_s3_ha": "PENDING_EXTERNAL",
    "ingress_and_monitoring_namespace_labels": "PENDING_EXTERNAL",
    "server_side_dry_run": "PENDING_EXTERNAL",
    "fault_domain_exercise": "PENDING_EXTERNAL",
}


class BundleError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def source_state() -> dict[str, str]:
    dirty = run_git("status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise BundleError("working tree must be clean before preparing a target HA bundle")
    return {
        "commit": run_git("rev-parse", "HEAD"),
        "tree": run_git("rev-parse", "HEAD^{tree}"),
        "branch": run_git("branch", "--show-current") or "DETACHED",
    }


def validate_output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if resolved.exists():
        raise BundleError("output directory must not already exist")
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise BundleError("output directory must be outside the repository")
    return resolved


def validate_image(value: str, *, field_name: str) -> str:
    if not IMAGE_RE.fullmatch(value):
        raise BundleError(
            f"{field_name} must be a lowercase registry image pinned by @sha256"
        )
    registry = value.split("/", 1)[0].split(":", 1)[0]
    if (
        "example." in value
        or registry == "localhost"
        or registry.endswith((".invalid", ".example", ".test", ".localhost"))
    ):
        raise BundleError(f"{field_name} still uses a placeholder or local registry")
    return value


def validate_k8s_name(
    value: str, *, field_name: str, dns_label: bool = False
) -> str:
    pattern = DNS_LABEL_RE if dns_label else K8S_NAME_RE
    if len(value) > 63 or not pattern.fullmatch(value):
        raise BundleError(f"{field_name} must be a Kubernetes DNS name of at most 63 characters")
    return value


def validate_public_host(value: str) -> str:
    host = value.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BundleError("public host must be a DNS name, not an IP address")
    labels = host.split(".")
    if len(labels) < 2 or any(len(label) > 63 or not DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise BundleError("public host must be a valid multi-label production DNS name")
    reserved = ("example.com", "example.net", "example.org")
    if host.endswith((".invalid", ".example", ".test", ".localhost")) or any(
        host == suffix or host.endswith(f".{suffix}") for suffix in reserved
    ):
        raise BundleError("public host must not use a reserved placeholder suffix")
    return host


def validate_source(source: Any) -> dict[str, str]:
    if not isinstance(source, dict):
        raise BundleError("source identity must be an object")
    commit = source.get("commit")
    tree = source.get("tree")
    branch = source.get("branch")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise BundleError("source commit must be a full lowercase Git object ID")
    if not isinstance(tree, str) or re.fullmatch(r"[0-9a-f]{40}", tree) is None:
        raise BundleError("source tree must be a full lowercase Git object ID")
    if not isinstance(branch, str) or not branch or len(branch) > 255:
        raise BundleError("source branch must be a non-empty bounded string")
    return {"commit": commit, "tree": tree, "branch": branch}


def validate_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise BundleError("target configuration must be an object")
    expected_keys = {
        "namespace",
        "backend_image",
        "frontend_image",
        "public_host",
        "runtime_secret",
        "tls_secret",
        "rwx_claim",
        "rwx_storage_class",
        "rwx_size",
        "egress_cidrs",
    }
    if set(target) != expected_keys:
        raise BundleError("target configuration has missing or unexpected fields")
    egress_cidrs = target["egress_cidrs"]
    if not isinstance(egress_cidrs, list) or not all(
        isinstance(item, str) for item in egress_cidrs
    ):
        raise BundleError("target egress CIDRs must be a string list")
    return {
        "namespace": validate_k8s_name(
            target["namespace"], field_name="namespace", dns_label=True
        ),
        "backend_image": validate_image(
            target["backend_image"], field_name="backend image"
        ),
        "frontend_image": validate_image(
            target["frontend_image"], field_name="frontend image"
        ),
        "public_host": validate_public_host(target["public_host"]),
        "runtime_secret": validate_k8s_name(
            target["runtime_secret"], field_name="runtime Secret"
        ),
        "tls_secret": validate_k8s_name(
            target["tls_secret"], field_name="TLS Secret"
        ),
        "rwx_claim": validate_k8s_name(target["rwx_claim"], field_name="RWX claim"),
        "rwx_storage_class": validate_k8s_name(
            target["rwx_storage_class"], field_name="RWX StorageClass"
        ),
        "rwx_size": validate_storage_size(target["rwx_size"]),
        "egress_cidrs": validate_egress_cidrs(egress_cidrs),
    }


def validate_storage_size(value: str) -> str:
    match = SIZE_RE.fullmatch(value)
    if match is None:
        raise BundleError("RWX size must be an integer Gi or Ti quantity")
    gibibytes = int(match.group("count")) * (1024 if match.group("unit") == "Ti" else 1)
    if gibibytes < 10:
        raise BundleError("RWX size must be at least 10Gi")
    return value


def validate_egress_cidrs(values: list[str]) -> list[str]:
    if not values or len(values) > 32:
        raise BundleError("between 1 and 32 approved egress CIDRs are required")
    blocked = [
        ipaddress.ip_network("0.0.0.0/8"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("224.0.0.0/4"),
        ipaddress.ip_network("::/128"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fe80::/10"),
        ipaddress.ip_network("ff00::/8"),
    ]
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise BundleError(f"invalid canonical egress CIDR: {value}") from exc
        minimum_prefix = 8 if network.version == 4 else 16
        if network.prefixlen < minimum_prefix:
            raise BundleError(f"egress CIDR is too broad: {network}")
        if any(network.version == item.version and network.overlaps(item) for item in blocked):
            raise BundleError(f"egress CIDR overlaps a forbidden network: {network}")
        networks.append(network)
    if len(set(networks)) != len(networks):
        raise BundleError("egress CIDRs must be unique")
    return [str(network) for network in sorted(networks, key=lambda item: (item.version, int(item.network_address), item.prefixlen))]


def render_base() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["kubectl", "kustomize", str(HA_BASE)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BundleError(f"kubectl kustomize failed: {completed.stderr.strip()}")
    documents = [item for item in yaml.safe_load_all(completed.stdout) if isinstance(item, dict)]
    if not documents:
        raise BundleError("HA base rendered no Kubernetes resources")
    return documents


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise BundleError("resource metadata must be an object")
    return metadata


def _pod_spec(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("kind") == "Job":
        return document["spec"]["template"]["spec"]
    return document["spec"]["template"]["spec"]


def _pod_template(document: dict[str, Any]) -> dict[str, Any]:
    return document["spec"]["template"]


def _component(document: dict[str, Any]) -> str:
    return str(_metadata(document).get("name", ""))


def prepare_documents(
    documents: list[dict[str, Any]],
    *,
    source: dict[str, str],
    namespace: str,
    backend_image: str,
    frontend_image: str,
    public_host: str,
    runtime_secret: str,
    tls_secret: str,
    rwx_claim: str,
    rwx_storage_class: str,
    rwx_size: str,
    egress_cidrs: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    commit = source["commit"]
    migration_name = f"duckdock-migrate-{commit[:12]}"
    phases = {name: [] for name in PHASE_FILES}

    for document in documents:
        kind = str(document.get("kind", ""))
        metadata = _metadata(document)
        name = str(metadata.get("name", ""))
        annotations = metadata.setdefault("annotations", {})
        annotations["duckdock.io/release-commit"] = commit
        if kind == "Namespace":
            metadata["name"] = namespace
        else:
            metadata["namespace"] = namespace

        if kind in {"Job", "Deployment"}:
            component = "migrate" if kind == "Job" else name
            pod_template = _pod_template(document)
            template_annotations = pod_template.setdefault("metadata", {}).setdefault(
                "annotations", {}
            )
            template_annotations["duckdock.io/release-commit"] = commit
            pod_spec = _pod_spec(document)
            containers = pod_spec.get("containers") or []
            if len(containers) != 1:
                raise BundleError(f"{kind}/{name} must contain exactly one primary container")
            if component == "frontend":
                containers[0]["image"] = frontend_image
                template_annotations["duckdock.io/image-digest"] = frontend_image.rsplit("@", 1)[1]
            else:
                containers[0]["image"] = backend_image
                template_annotations["duckdock.io/image-digest"] = backend_image.rsplit("@", 1)[1]
            if kind == "Job":
                metadata["name"] = migration_name
            if component != "frontend":
                env_from = containers[0].get("envFrom") or []
                if len(env_from) != 1 or "secretRef" not in env_from[0]:
                    raise BundleError(f"{kind}/{name} must use one runtime Secret reference")
                env_from[0]["secretRef"]["name"] = runtime_secret
            if component in {"backend", "worker"}:
                volumes = pod_spec.get("volumes") or []
                claim_volumes = [item for item in volumes if "persistentVolumeClaim" in item]
                if len(claim_volumes) != 1:
                    raise BundleError(f"Deployment/{component} must use exactly one RWX claim")
                claim_volumes[0]["persistentVolumeClaim"]["claimName"] = rwx_claim

        if kind == "Ingress":
            document["spec"]["ingressClassName"] = "nginx"
            document["spec"]["tls"][0]["hosts"] = [public_host]
            document["spec"]["tls"][0]["secretName"] = tls_secret
            document["spec"]["rules"][0]["host"] = public_host

        if kind == "NetworkPolicy" and name == "controlled-external-egress":
            egress = document["spec"].get("egress") or []
            if len(egress) != 1:
                raise BundleError("controlled external egress must contain exactly one rule")
            egress[0]["to"] = [{"ipBlock": {"cidr": cidr}} for cidr in egress_cidrs]

        if kind == "Job":
            phases["migration.yaml"].append(document)
        elif kind in PHASE_FILES["bootstrap.yaml"]:
            phases["bootstrap.yaml"].append(document)
        else:
            phases["applications.yaml"].append(document)

    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": rwx_claim,
            "namespace": namespace,
            "annotations": {"duckdock.io/release-commit": commit},
            "labels": {
                "app.kubernetes.io/name": "duckdock",
                "app.kubernetes.io/part-of": "duckdock",
            },
        },
        "spec": {
            "accessModes": ["ReadWriteMany"],
            "storageClassName": rwx_storage_class,
            "resources": {"requests": {"storage": rwx_size}},
        },
    }
    phases["bootstrap.yaml"].append(pvc)
    return phases, migration_name


def dump_documents(documents: list[dict[str, Any]]) -> str:
    return yaml.safe_dump_all(
        documents,
        explicit_start=True,
        sort_keys=False,
        default_flow_style=False,
    )


def _load_phase(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise BundleError(f"bundle phase must be a regular file: {path.name}")
    return [item for item in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(item, dict)]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)


def verify_documents(
    phases: dict[str, list[dict[str, Any]]],
    *,
    source: dict[str, str],
    target: dict[str, Any],
    migration_name: str,
) -> dict[str, Any]:
    documents = [document for phase in PHASE_FILES for document in phases[phase]]
    counts = Counter(str(document.get("kind")) for document in documents)
    _require(dict(counts) == EXPECTED_KIND_COUNTS, f"unexpected resource inventory: {dict(counts)}")
    _require(not (set(counts) & STATEFUL_BLOCKED_KINDS), "bundle must not embed Secret or StatefulSet resources")

    namespace = target["namespace"]
    commit = source["commit"]
    backend_image = target["backend_image"]
    frontend_image = target["frontend_image"]
    runtime_secret = target["runtime_secret"]
    rwx_claim = target["rwx_claim"]
    deployments = {
        str(item.get("metadata", {}).get("name"))
        for item in documents
        if item.get("kind") == "Deployment"
    }
    services_by_name = {
        str(item.get("metadata", {}).get("name"))
        for item in documents
        if item.get("kind") == "Service"
    }
    policy_names = {
        str(item.get("metadata", {}).get("name"))
        for item in documents
        if item.get("kind") == "NetworkPolicy"
    }
    _require(deployments == EXPECTED_DEPLOYMENTS, "Deployment inventory changed")
    _require(services_by_name == EXPECTED_SERVICES, "Service inventory changed")
    _require(policy_names == EXPECTED_POLICIES, "NetworkPolicy inventory changed")
    for phase_name, allowed_kinds in PHASE_FILES.items():
        _require(
            all(str(document.get("kind")) in allowed_kinds for document in phases[phase_name]),
            f"{phase_name} contains a resource from another deployment phase",
        )
    for document in documents:
        kind = str(document.get("kind"))
        metadata = _metadata(document)
        _require(
            metadata.get("annotations", {}).get("duckdock.io/release-commit") == commit,
            f"{kind}/{metadata.get('name')} is not bound to the source commit",
        )
        if kind == "Namespace":
            _require(metadata.get("name") == namespace, "Namespace does not match target")
            labels = metadata.get("labels") or {}
            _require(
                labels.get("pod-security.kubernetes.io/enforce") == "restricted",
                "Namespace must enforce the restricted Pod Security standard",
            )
        else:
            _require(metadata.get("namespace") == namespace, f"{kind}/{metadata.get('name')} has wrong Namespace")

    workloads = [item for item in documents if item.get("kind") in {"Job", "Deployment"}]
    for workload in workloads:
        kind = str(workload["kind"])
        name = str(workload["metadata"]["name"])
        component = "migrate" if kind == "Job" else name
        pod_template = _pod_template(workload)
        pod_spec = _pod_spec(workload)
        _require(pod_spec.get("serviceAccountName") == "duckdock", f"{kind}/{name} has wrong ServiceAccount")
        _require(pod_spec.get("automountServiceAccountToken") is False, f"{kind}/{name} mounts a service-account token")
        pod_security = pod_spec.get("securityContext") or {}
        _require(pod_security.get("runAsNonRoot") is True, f"{kind}/{name} is not non-root")
        _require(
            (pod_security.get("seccompProfile") or {}).get("type") == "RuntimeDefault",
            f"{kind}/{name} lacks RuntimeDefault seccomp",
        )
        containers = pod_spec.get("containers") or []
        _require(len(containers) == 1, f"{kind}/{name} has unexpected container count")
        container = containers[0]
        expected_image = frontend_image if component == "frontend" else backend_image
        _require(container.get("image") == expected_image, f"{kind}/{name} image is not the approved digest")
        security = container.get("securityContext") or {}
        _require(security.get("allowPrivilegeEscalation") is False, f"{kind}/{name} permits privilege escalation")
        _require(security.get("readOnlyRootFilesystem") is True, f"{kind}/{name} root filesystem is writable")
        _require((security.get("capabilities") or {}).get("drop") == ["ALL"], f"{kind}/{name} does not drop all capabilities")
        resources = container.get("resources") or {}
        _require(resources.get("requests") and resources.get("limits"), f"{kind}/{name} lacks resource bounds")
        _require(
            pod_template.get("metadata", {}).get("annotations", {}).get("duckdock.io/release-commit") == commit,
            f"{kind}/{name} Pod template is not release-bound",
        )
        _require(
            pod_template.get("metadata", {})
            .get("annotations", {})
            .get("duckdock.io/image-digest")
            == expected_image.rsplit("@", 1)[1],
            f"{kind}/{name} Pod template image annotation does not match",
        )
        if component != "frontend":
            env_from = container.get("envFrom") or []
            _require(
                len(env_from) == 1
                and env_from[0].get("secretRef", {}).get("name") == runtime_secret,
                f"{kind}/{name} does not reference the approved runtime Secret",
            )
        if component in {"backend", "worker"}:
            claims = [
                volume.get("persistentVolumeClaim", {}).get("claimName")
                for volume in pod_spec.get("volumes") or []
                if "persistentVolumeClaim" in volume
            ]
            _require(claims == [rwx_claim], f"Deployment/{component} does not use the approved RWX claim")
        if component in REPLICATED_COMPONENTS:
            deployment = workload.get("spec") or {}
            _require(deployment.get("replicas") == 3, f"Deployment/{component} must start with three replicas")
            constraints = pod_spec.get("topologySpreadConstraints") or []
            zone = next((item for item in constraints if item.get("topologyKey") == "topology.kubernetes.io/zone"), None)
            _require(
                zone is not None
                and zone.get("whenUnsatisfiable") == "DoNotSchedule"
                and zone.get("nodeTaintsPolicy") == "Honor"
                and zone.get("matchLabelKeys") == ["pod-template-hash"],
                f"Deployment/{component} lacks strict revision-aware zone spreading",
            )
    job = next(item for item in workloads if item.get("kind") == "Job")
    _require(
        migration_name == f"duckdock-migrate-{commit[:12]}"
        and job["metadata"]["name"] == migration_name,
        "migration Job is not commit-bound",
    )
    beat = next(item for item in workloads if _component(item) == "beat")
    _require(beat["spec"].get("replicas") == 1, "Celery Beat must remain a singleton")
    _require(beat["spec"].get("strategy", {}).get("type") == "Recreate", "Celery Beat must use Recreate")

    pvc = next(item for item in documents if item.get("kind") == "PersistentVolumeClaim")
    _require(pvc["metadata"]["name"] == rwx_claim, "RWX PVC name does not match target")
    _require(pvc["spec"].get("accessModes") == ["ReadWriteMany"], "repository PVC must require ReadWriteMany")
    _require(pvc["spec"].get("storageClassName") == target["rwx_storage_class"], "RWX StorageClass does not match target")
    _require(
        pvc["spec"].get("resources", {}).get("requests", {}).get("storage") == target["rwx_size"],
        "RWX storage request does not match target",
    )

    ingress = next(item for item in documents if item.get("kind") == "Ingress")
    _require(ingress["spec"].get("ingressClassName") == "nginx", "target bundle requires the reviewed nginx Ingress class")
    _require(ingress["spec"]["rules"][0].get("host") == target["public_host"], "Ingress host does not match target")
    _require(ingress["spec"]["tls"][0].get("hosts") == [target["public_host"]], "Ingress TLS host does not match target")
    _require(ingress["spec"]["tls"][0].get("secretName") == target["tls_secret"], "Ingress TLS Secret does not match target")

    services = [item for item in documents if item.get("kind") == "Service"]
    _require(all(item.get("spec", {}).get("type") in {None, "ClusterIP"} for item in services), "application Services must remain internal ClusterIP")

    policies = [item for item in documents if item.get("kind") == "NetworkPolicy"]
    controlled = next(item for item in policies if item["metadata"]["name"] == "controlled-external-egress")
    observed_cidrs = [
        item["ipBlock"]["cidr"] for item in controlled["spec"]["egress"][0].get("to") or []
    ]
    _require(observed_cidrs == target["egress_cidrs"], "controlled egress CIDRs do not match target")
    validate_egress_cidrs(observed_cidrs)
    _require(any(item["metadata"]["name"] == "default-deny" for item in policies), "default-deny NetworkPolicy is missing")

    pdbs = [item for item in documents if item.get("kind") == "PodDisruptionBudget"]
    _require(all(item["spec"].get("minAvailable") == 2 for item in pdbs), "PDB minAvailable must be two")
    hpas = [item for item in documents if item.get("kind") == "HorizontalPodAutoscaler"]
    _require(all(int(item["spec"].get("minReplicas", 0)) >= 3 for item in hpas), "HPA minReplicas must be at least three")

    serialized = dump_documents(documents)
    for forbidden in ("example.invalid", "sha256:0000000000000000", "0.0.0.0/0", "__CHANGE_ME"):
        _require(forbidden not in serialized, f"target bundle retains forbidden placeholder: {forbidden}")
    return {"resource_count": len(documents), "kind_counts": dict(sorted(counts.items()))}


def harden_tree(directory: Path) -> None:
    for current_root, directory_names, file_names in os.walk(directory, followlinks=False):
        current = Path(current_root)
        if current.is_symlink():
            raise BundleError("bundle output directory must not be a symbolic link")
        current.chmod(0o700)
        for name in directory_names:
            child = current / name
            if child.is_symlink():
                raise BundleError("bundle output directory must not contain symbolic links")
            child.chmod(0o700)
        for name in file_names:
            child = current / name
            if child.is_symlink():
                raise BundleError("bundle output must not contain symbolic links")
            child.chmod(0o600)


def apply_sequence(migration_name: str) -> list[str]:
    return [
        "server-side dry-run bootstrap.yaml, migration.yaml and applications.yaml",
        "apply bootstrap.yaml and verify externally managed runtime/TLS Secrets",
        f"apply migration.yaml and wait for Job/{migration_name} completion",
        "apply applications.yaml and wait for all four Deployment rollouts",
        "execute target network, capacity, state-service and zone-failure GA collectors",
    ]


def prepare_bundle(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = validate_output_dir(args.output_dir)
    source = validate_source(source_state())
    target = validate_target(
        {
            "namespace": args.namespace,
            "backend_image": args.backend_image,
            "frontend_image": args.frontend_image,
            "public_host": args.public_host,
            "runtime_secret": args.runtime_secret,
            "tls_secret": args.tls_secret,
            "rwx_claim": args.rwx_claim,
            "rwx_storage_class": args.rwx_storage_class,
            "rwx_size": args.rwx_size,
            "egress_cidrs": args.egress_cidr,
        }
    )
    phases, migration_name = prepare_documents(
        render_base(),
        source=source,
        namespace=target["namespace"],
        backend_image=target["backend_image"],
        frontend_image=target["frontend_image"],
        public_host=target["public_host"],
        runtime_secret=target["runtime_secret"],
        tls_secret=target["tls_secret"],
        rwx_claim=target["rwx_claim"],
        rwx_storage_class=target["rwx_storage_class"],
        rwx_size=target["rwx_size"],
        egress_cidrs=target["egress_cidrs"],
    )
    verification = verify_documents(phases, source=source, target=target, migration_name=migration_name)

    output_dir.mkdir(mode=0o700, parents=True)
    files: list[dict[str, Any]] = []
    for name in PHASE_FILES:
        path = output_dir / name
        path.write_text(dump_documents(phases[name]), encoding="utf-8")
        files.append(
            {
                "path": name,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "resource_count": len(phases[name]),
            }
        )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "PREPARED_NOT_AUTHORIZED",
        "authorization_scope": AUTHORIZATION_SCOPE,
        "prepared_at": utc_now(),
        "source": source,
        "target": target,
        "migration_job": migration_name,
        "files": files,
        "verification": verification,
        "apply_sequence": apply_sequence(migration_name),
        "external_requirements": EXTERNAL_REQUIREMENTS,
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = sha256_file(receipt_path)
    (output_dir / "receipt.sha256").write_text(f"{digest}  receipt.json\n", encoding="utf-8")
    harden_tree(output_dir)
    verify_bundle(output_dir)
    return {**receipt, "receipt_sha256": digest, "output_dir": str(output_dir)}


def verify_bundle(bundle_dir: Path) -> dict[str, Any]:
    unresolved = bundle_dir.expanduser()
    if unresolved.is_symlink():
        raise BundleError("bundle directory must not be a symbolic link")
    directory = unresolved.resolve(strict=True)
    if not directory.is_dir():
        raise BundleError("bundle directory must be a real directory")
    _require(directory.stat().st_mode & 0o077 == 0, "bundle directory is accessible by group or others")
    expected_children = {*PHASE_FILES, "receipt.json", "receipt.sha256"}
    actual_children = {item.name for item in directory.iterdir()}
    _require(actual_children == expected_children, "bundle directory has missing or unexpected entries")
    _require(
        all(not item.is_symlink() and item.is_file() for item in directory.iterdir()),
        "bundle directory may contain regular files only",
    )
    receipt_path = directory / "receipt.json"
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise BundleError("bundle receipt is missing or symbolic")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    _require(
        set(receipt)
        == {
            "schema_version",
            "status",
            "authorization_scope",
            "prepared_at",
            "source",
            "target",
            "migration_job",
            "files",
            "verification",
            "apply_sequence",
            "external_requirements",
        },
        "bundle receipt has missing or unexpected fields",
    )
    _require(receipt.get("schema_version") == SCHEMA_VERSION, "unsupported HA bundle schema")
    _require(receipt.get("status") == "PREPARED_NOT_AUTHORIZED", "bundle has an invalid status")
    _require(receipt.get("authorization_scope") == AUTHORIZATION_SCOPE, "bundle authorization boundary changed")
    source = validate_source(receipt.get("source"))
    target = validate_target(receipt.get("target"))
    _require(source == receipt.get("source"), "source identity is not canonical")
    _require(target == receipt.get("target"), "target configuration is not canonical")
    _require(
        receipt.get("migration_job") == f"duckdock-migrate-{source['commit'][:12]}",
        "receipt migration Job is not derived from its source commit",
    )
    _require(
        receipt.get("apply_sequence") == apply_sequence(receipt["migration_job"]),
        "bundle apply sequence changed",
    )
    _require(
        receipt.get("external_requirements") == EXTERNAL_REQUIREMENTS,
        "bundle external requirements changed",
    )
    expected_names = list(PHASE_FILES)
    entries = receipt.get("files")
    _require(isinstance(entries, list) and [item.get("path") for item in entries] == expected_names, "bundle file manifest is not exact")
    phases: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        _require(
            isinstance(entry, dict)
            and set(entry) == {"path", "sha256", "size", "resource_count"},
            "bundle file entry has missing or unexpected fields",
        )
        path = directory / entry["path"]
        _require(path.parent == directory, "bundle file escaped its directory")
        _require(path.stat().st_mode & 0o077 == 0, f"bundle file is accessible by group or others: {path.name}")
        _require(sha256_file(path) == entry.get("sha256"), f"bundle digest mismatch: {path.name}")
        _require(path.stat().st_size == entry.get("size"), f"bundle size mismatch: {path.name}")
        phases[path.name] = _load_phase(path)
        _require(len(phases[path.name]) == entry.get("resource_count"), f"bundle resource count mismatch: {path.name}")
    verification = verify_documents(
        phases,
        source=source,
        target=target,
        migration_name=receipt["migration_job"],
    )
    _require(verification == receipt.get("verification"), "persisted bundle verification projection changed")
    sidecar = directory / "receipt.sha256"
    _require(sidecar.is_file() and not sidecar.is_symlink(), "receipt sidecar is missing or symbolic")
    expected_sidecar = f"{sha256_file(receipt_path)}  receipt.json\n"
    _require(sidecar.read_text(encoding="utf-8") == expected_sidecar, "receipt sidecar does not match")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "TARGET_HA_BUNDLE_VERIFIED_NOT_AUTHORIZED",
        "authorization_scope": AUTHORIZATION_SCOPE,
        "receipt_sha256": sha256_file(receipt_path),
        "source": source,
        "target": target,
        "migration_job": receipt["migration_job"],
        "verification": verification,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Prepare a new target HA bundle")
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--namespace", default="duckdock")
    prepare.add_argument("--backend-image", required=True)
    prepare.add_argument("--frontend-image", required=True)
    prepare.add_argument("--public-host", required=True)
    prepare.add_argument("--runtime-secret", default="duckdock-runtime-secrets")
    prepare.add_argument("--tls-secret", default="duckdock-ingress-tls")
    prepare.add_argument("--rwx-claim", default="duckdock-repos-rwx")
    prepare.add_argument("--rwx-storage-class", required=True)
    prepare.add_argument("--rwx-size", default="100Gi")
    prepare.add_argument("--egress-cidr", action="append", required=True)
    verify = subparsers.add_parser("verify", help="Verify an existing target HA bundle")
    verify.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = prepare_bundle(args) if args.command == "prepare" else verify_bundle(args.bundle_dir)
    except (BundleError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"HA target bundle refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
