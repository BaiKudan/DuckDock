from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SCRIPT = REPO_ROOT / "scripts" / "prepare-kubernetes-ha-target.py"
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy-kubernetes-ha-target.py"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ha_bundle = _load("duckdock_ha_bundle_deployment_test", BUNDLE_SCRIPT)
ha_deploy = _load("duckdock_ha_target_deployment_test", DEPLOY_SCRIPT)

SOURCE = {
    "commit": "a" * 40,
    "tree": "b" * 40,
    "branch": "codex/release-2.0-rc1",
}
BACKEND_IMAGE = "registry.acme.cn/duckdock/backend@sha256:" + "c" * 64
FRONTEND_IMAGE = "registry.acme.cn/duckdock/frontend@sha256:" + "d" * 64
CONTEXT = "duckdock-prod-admin"
CLUSTER_UID = "5b13e1c1-7b37-42fd-a4c4-d83d73b80a01"
PRINCIPAL = "duckdock-deployer@acme.cn"
NAMESPACE = "duckdock"


def _bundle_args(output_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        output_dir=output_dir,
        namespace=NAMESPACE,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        public_host="duckdock.acme.cn",
        runtime_secret="duckdock-runtime-secrets",
        tls_secret="duckdock-ingress-tls",
        rwx_claim="duckdock-repos-rwx",
        rwx_storage_class="cephfs-rwx",
        rwx_size="100Gi",
        egress_cidr=["10.20.0.0/16", "172.20.0.0/16"],
    )


def _prepared_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.setattr(ha_bundle, "source_state", lambda: SOURCE.copy())
    output = tmp_path / "target-bundle"
    ha_bundle.prepare_bundle(_bundle_args(output))
    return output


class FakeCluster:
    def __init__(
        self,
        *,
        cluster_uid: str = CLUSTER_UID,
        zones: int = 3,
        omit_runtime_key: str | None = None,
        deny_required_permission: tuple[str, str] | None = None,
        fail_stage: str | None = None,
    ) -> None:
        self.cluster_uid = cluster_uid
        self.zones = zones
        self.omit_runtime_key = omit_runtime_key
        self.deny_required_permission = deny_required_permission
        self.fail_stage = fail_stage
        self.calls: list[list[str]] = []
        target = {
            "namespace": NAMESPACE,
            "runtime_secret": "duckdock-runtime-secrets",
            "tls_secret": "duckdock-ingress-tls",
            "rwx_storage_class": "cephfs-rwx",
        }
        self.permission_expectations = {
            (check.verb, check.resource, check.namespace): check.expectation == "ALLOW"
            for check in ha_deploy.deployment_permission_profile(target)
        }

    @staticmethod
    def _result(argv: list[str], stdout: bytes, exit_code: int = 0) -> Any:
        return ha_deploy.CommandResult(
            argv=argv,
            exit_code=exit_code,
            stdout=stdout,
            stderr=b"" if exit_code == 0 else b"refused",
            started_at="2026-08-07T06:00:00+00:00",
            finished_at="2026-08-07T06:00:00.010000+00:00",
            duration_ms=10,
        )

    @staticmethod
    def _json(argv: list[str], value: dict[str, Any]) -> Any:
        return FakeCluster._result(argv, json.dumps(value).encode())

    def __call__(self, argv: list[str], _timeout_seconds: int) -> Any:
        self.calls.append(argv)
        args = argv[4:]
        if self.fail_stage == "apply_applications" and args[:2] == ["apply", "--server-side"]:
            file_path = args[args.index("-f") + 1]
            if file_path.endswith("applications.yaml") and "--dry-run=server" not in args:
                return self._result(argv, b"", exit_code=1)
        if args[:4] == ["auth", "whoami", "-o", "json"]:
            return self._json(argv, {"status": {"userInfo": {"username": PRINCIPAL}}})
        if args[:4] == ["get", "namespace", "kube-system", "-o"]:
            return self._json(argv, {"metadata": {"name": "kube-system", "uid": self.cluster_uid}})
        if args[:3] == ["version", "-o", "json"]:
            return self._json(
                argv,
                {"serverVersion": {"major": "1", "minor": "34+", "gitVersion": "v1.34.2"}},
            )
        if args[:3] == ["get", "namespace", NAMESPACE]:
            return self._json(
                argv,
                {
                    "metadata": {
                        "name": NAMESPACE,
                        "uid": "namespace-uid",
                        "resourceVersion": "101",
                        "labels": {
                            "pod-security.kubernetes.io/enforce": "restricted",
                            "pod-security.kubernetes.io/audit": "restricted",
                            "pod-security.kubernetes.io/warn": "restricted",
                        },
                    }
                },
            )
        if args[:3] == ["get", "nodes", "-o"]:
            items = []
            for index in range(self.zones):
                items.append(
                    {
                        "metadata": {
                            "name": f"node-{index}",
                            "labels": {
                                "topology.kubernetes.io/zone": f"zone-{index}",
                                "kubernetes.io/hostname": f"node-{index}",
                            },
                        },
                        "spec": {"unschedulable": False},
                        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                    }
                )
            return self._json(argv, {"items": items})
        if args[:3] == ["get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes"]:
            return self._json(argv, {"items": [{"metadata": {"name": f"node-{i}"}} for i in range(self.zones)]})
        if args[:2] == ["get", "storageclass.storage.k8s.io/cephfs-rwx"]:
            return self._json(
                argv,
                {
                    "metadata": {"name": "cephfs-rwx"},
                    "provisioner": "rwx.csi.ceph.com",
                    "volumeBindingMode": "WaitForFirstConsumer",
                    "allowVolumeExpansion": True,
                },
            )
        if args[:3] == ["get", "secret", "duckdock-runtime-secrets"]:
            keys = set(ha_deploy.REQUIRED_RUNTIME_SECRET_KEYS)
            if self.omit_runtime_key:
                keys.remove(self.omit_runtime_key)
            payload = "301\nOpaque\n" + "\n".join(sorted(keys)) + "\n"
            return self._result(argv, payload.encode())
        if args[:3] == ["get", "secret", "duckdock-ingress-tls"]:
            return self._result(argv, b"302\nkubernetes.io/tls\ntls.crt\ntls.key\n")
        if args[:2] == ["get", "namespaces"] and "--selector" in args:
            selector = args[args.index("--selector") + 1]
            name = "ingress-nginx" if selector == "duckdock.io/ingress=true" else "monitoring"
            return self._json(argv, {"items": [{"metadata": {"name": name}}]})
        if args[:2] == ["auth", "can-i"]:
            verb = args[2]
            resource = args[3]
            namespace = None
            if "--all-namespaces" in args:
                namespace = "*"
            elif "--namespace" in args:
                namespace = args[args.index("--namespace") + 1]
            allowed = self.permission_expectations[(verb, resource, namespace)]
            if self.deny_required_permission == (verb, resource):
                allowed = False
            return self._result(argv, b"yes\n" if allowed else b"no\n")
        if args[:2] == ["apply", "--server-side"]:
            return self._result(argv, b"configured\n")
        if args[:1] in (["wait"], ["rollout"]):
            return self._result(argv, b"condition met\n")
        if args[:3] == ["get", "--namespace", NAMESPACE] and args[3].startswith("job/"):
            return self._json(
                argv,
                {
                    "metadata": {
                        "name": "duckdock-migrate-aaaaaaaaaaaa",
                        "uid": "job-uid",
                        "annotations": {"duckdock.io/release-commit": SOURCE["commit"]},
                    },
                    "spec": {
                        "template": {
                            "spec": {"containers": [{"image": BACKEND_IMAGE}]}
                        }
                    },
                    "status": {
                        "succeeded": 1,
                        "failed": 0,
                        "conditions": [{"type": "Complete", "status": "True"}],
                    },
                },
            )
        if args[:3] == ["get", "--namespace", NAMESPACE] and args[3].startswith("deployment/"):
            name = args[3].split("/", 1)[1]
            replicas = 1 if name == "beat" else 3
            image = FRONTEND_IMAGE if name == "frontend" else BACKEND_IMAGE
            return self._json(
                argv,
                {
                    "metadata": {
                        "name": name,
                        "uid": f"{name}-uid",
                        "generation": 7,
                        "annotations": {"duckdock.io/release-commit": SOURCE["commit"]},
                    },
                    "spec": {
                        "replicas": replicas,
                        "template": {"spec": {"containers": [{"image": image}]}},
                    },
                    "status": {
                        "observedGeneration": 7,
                        "updatedReplicas": replicas,
                        "availableReplicas": replicas,
                        "unavailableReplicas": 0,
                    },
                },
            )
        raise AssertionError(f"unexpected kubectl arguments: {args}")


def _create_preflight(
    tmp_path: Path,
    bundle_dir: Path,
    cluster: FakeCluster,
    now: datetime,
) -> Path:
    receipt = ha_deploy.collect_preflight(
        bundle_dir,
        context=CONTEXT,
        expected_cluster_uid=CLUSTER_UID,
        expected_principal=PRINCIPAL,
        runner=cluster,
        now=now,
    )
    path = tmp_path / "preflight.json"
    ha_deploy.write_receipt(path, receipt)
    return path


def test_preflight_binds_target_and_server_side_dry_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    cluster = FakeCluster()

    receipt_path = _create_preflight(tmp_path, bundle_dir, cluster, now)
    verified = ha_deploy.verify_preflight(
        receipt_path,
        bundle_dir=bundle_dir,
        now=now,
    )

    assert verified["status"] == "TARGET_HA_PREFLIGHT_VERIFIED_NOT_GA_AUTHORIZED"
    assert verified["kubernetes"]["identity"] == {
        "context": CONTEXT,
        "namespace": NAMESPACE,
        "cluster_uid": CLUSTER_UID,
        "principal": PRINCIPAL,
    }
    assert verified["kubernetes"]["fault_domains"]["zones"] == {
        "zone-0": 1,
        "zone-1": 1,
        "zone-2": 1,
    }
    dry_runs = [
        call for call in cluster.calls if "--dry-run=server" in call
    ]
    assert len(dry_runs) == 3
    assert set(verified["validated_requirements"].values()) == {"PASS"}
    assert set(verified["remaining_external_requirements"].values()) == {
        "PENDING_EXTERNAL"
    }


def test_preflight_offline_verification_survives_age_but_mutation_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)
    observed_at = datetime.now(timezone.utc).replace(microsecond=0)
    receipt_path = _create_preflight(
        tmp_path,
        bundle_dir,
        FakeCluster(),
        observed_at,
    )
    later = observed_at + ha_deploy.PREFLIGHT_MAX_AGE + timedelta(minutes=1)

    assert ha_deploy.verify_preflight(
        receipt_path,
        now=later,
    )["status"] == "TARGET_HA_PREFLIGHT_VERIFIED_NOT_GA_AUTHORIZED"
    with pytest.raises(ha_deploy.TargetError, match="stale for target mutation"):
        ha_deploy.verify_preflight(
            receipt_path,
            now=later,
            require_fresh=True,
        )


def test_preflight_semantic_tamper_is_rejected_even_with_new_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)
    receipt_path = _create_preflight(
        tmp_path,
        bundle_dir,
        FakeCluster(),
        datetime.now(timezone.utc),
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["kubernetes"]["runtime_secret"]["key_names"].remove("DATABASE_URL")
    payload = json.dumps(receipt, indent=2, sort_keys=True).encode() + b"\n"
    receipt_path.write_bytes(payload)
    sidecar = ha_deploy.receipt_sidecar(receipt_path)
    sidecar.write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {receipt_path.name}\n",
        encoding="utf-8",
    )

    with pytest.raises(ha_deploy.TargetError, match="runtime_secret projection"):
        ha_deploy.verify_preflight(receipt_path)


@pytest.mark.parametrize(
    ("cluster", "match"),
    [
        (FakeCluster(cluster_uid="wrong-cluster"), "UID differs"),
        (FakeCluster(zones=2), "three Ready"),
        (
            FakeCluster(omit_runtime_key="DATABASE_URL"),
            "missing required key names: DATABASE_URL",
        ),
        (
            FakeCluster(deny_required_permission=("create", "deployments.apps")),
            "violates RBAC profile",
        ),
    ],
)
def test_preflight_rejects_target_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster: FakeCluster,
    match: str,
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)

    with pytest.raises(ha_deploy.TargetError, match=match):
        ha_deploy.collect_preflight(
            bundle_dir,
            context=CONTEXT,
            expected_cluster_uid=CLUSTER_UID,
            expected_principal=PRINCIPAL,
            runner=cluster,
        )


def test_deploy_runs_exact_phases_and_verifies_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    preflight_path = _create_preflight(tmp_path, bundle_dir, FakeCluster(), now)
    bundle_digest = ha_bundle.verify_bundle(bundle_dir)["receipt_sha256"]
    output = tmp_path / "deployment.json"

    result = ha_deploy.execute_deployment(
        bundle_dir=bundle_dir,
        preflight_receipt=preflight_path,
        context=CONTEXT,
        expected_cluster_uid=CLUSTER_UID,
        expected_principal=PRINCIPAL,
        change_request_id="CHG-20260807-001",
        confirmation=ha_deploy.mutation_confirmation(
            CONTEXT, NAMESPACE, bundle_digest
        ),
        output=output,
        runner=FakeCluster(),
        now=now,
    )

    assert result["status"] == ha_deploy.DEPLOYED_STATUS
    assert result["completed_phases"] == ["bootstrap", "migration", "applications"]
    assert result["migration"]["succeeded"] == 1
    assert {item["name"] for item in result["deployments"]} == set(
        ha_deploy.ALL_DEPLOYMENTS
    )
    verified = ha_deploy.verify_deployment(output)
    assert verified["status"] == f"{ha_deploy.DEPLOYED_STATUS}_VERIFIED"


def test_deploy_requires_exact_content_addressed_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    preflight_path = _create_preflight(tmp_path, bundle_dir, FakeCluster(), now)
    cluster = FakeCluster()
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(ha_deploy.TargetError, match="confirmation"):
        ha_deploy.execute_deployment(
            bundle_dir=bundle_dir,
            preflight_receipt=preflight_path,
            context=CONTEXT,
            expected_cluster_uid=CLUSTER_UID,
            expected_principal=PRINCIPAL,
            change_request_id="CHG-20260807-003",
            confirmation="APPLY_DUCKDOCK_HA_TARGET:wrong",
            output=output,
            runner=cluster,
            now=now,
        )

    assert not output.exists()
    assert cluster.calls == []


def test_partial_deployment_persists_immutable_incomplete_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    preflight_path = _create_preflight(tmp_path, bundle_dir, FakeCluster(), now)
    bundle_digest = ha_bundle.verify_bundle(bundle_dir)["receipt_sha256"]
    output = tmp_path / "deployment-incomplete.json"

    with pytest.raises(ha_deploy.TargetError, match="apply_applications"):
        ha_deploy.execute_deployment(
            bundle_dir=bundle_dir,
            preflight_receipt=preflight_path,
            context=CONTEXT,
            expected_cluster_uid=CLUSTER_UID,
            expected_principal=PRINCIPAL,
            change_request_id="CHG-20260807-002",
            confirmation=ha_deploy.mutation_confirmation(
                CONTEXT, NAMESPACE, bundle_digest
            ),
            output=output,
            runner=FakeCluster(fail_stage="apply_applications"),
            now=now,
        )

    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == ha_deploy.INCOMPLETE_STATUS
    assert receipt["completed_phases"] == ["bootstrap", "migration"]
    assert receipt["failure"] == {
        "stage": "apply_applications",
        "category": "TargetError",
    }
    assert ha_deploy.verify_deployment(output)["status"] == (
        f"{ha_deploy.INCOMPLETE_STATUS}_VERIFIED"
    )


def test_incomplete_receipt_rejects_tampered_rollout_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _prepared_bundle(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    preflight_path = _create_preflight(tmp_path, bundle_dir, FakeCluster(), now)
    bundle_digest = ha_bundle.verify_bundle(bundle_dir)["receipt_sha256"]
    output = tmp_path / "deployment-incomplete-tampered.json"

    with pytest.raises(ha_deploy.TargetError, match="apply_applications"):
        ha_deploy.execute_deployment(
            bundle_dir=bundle_dir,
            preflight_receipt=preflight_path,
            context=CONTEXT,
            expected_cluster_uid=CLUSTER_UID,
            expected_principal=PRINCIPAL,
            change_request_id="CHG-20260807-004",
            confirmation=ha_deploy.mutation_confirmation(
                CONTEXT, NAMESPACE, bundle_digest
            ),
            output=output,
            runner=FakeCluster(fail_stage="apply_applications"),
            now=now,
        )

    receipt = json.loads(output.read_text(encoding="utf-8"))
    receipt["deployments"] = [
        {
            "name": "frontend",
            "uid": "frontend-uid",
            "generation": 7,
            "replicas": 3,
            "available_replicas": 3,
            "image": FRONTEND_IMAGE,
        }
    ]
    payload = json.dumps(receipt, indent=2, sort_keys=True).encode() + b"\n"
    output.write_bytes(payload)
    ha_deploy.receipt_sidecar(output).write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {output.name}\n",
        encoding="utf-8",
    )

    with pytest.raises(ha_deploy.TargetError, match="exact rollout prefix"):
        ha_deploy.verify_deployment(output)
