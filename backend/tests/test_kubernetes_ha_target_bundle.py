from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "prepare-kubernetes-ha-target.py"
SPEC = importlib.util.spec_from_file_location("duckdock_ha_target_bundle", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
ha_bundle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ha_bundle
SPEC.loader.exec_module(ha_bundle)

SOURCE = {
    "commit": "a" * 40,
    "tree": "b" * 40,
    "branch": "codex/release-2.0-rc1",
}
BACKEND_IMAGE = "registry.acme.cn/duckdock/backend@sha256:" + "c" * 64
FRONTEND_IMAGE = "registry.acme.cn/duckdock/frontend@sha256:" + "d" * 64


def _args(output_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        output_dir=output_dir,
        namespace="duckdock",
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        public_host="duckdock.acme.cn",
        runtime_secret="duckdock-runtime-secrets",
        tls_secret="duckdock-ingress-tls",
        rwx_claim="duckdock-repos-rwx",
        rwx_storage_class="cephfs-rwx",
        rwx_size="100Gi",
        egress_cidr=["172.20.0.0/16", "10.20.0.0/16"],
    )


def test_target_input_validation_is_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ha_bundle.BundleError, match="pinned"):
        ha_bundle.validate_image("registry.acme.cn/duckdock/backend:latest", field_name="image")
    with pytest.raises(ha_bundle.BundleError, match="placeholder"):
        ha_bundle.validate_image(
            "registry.example.invalid/duckdock/backend@sha256:" + "a" * 64,
            field_name="image",
        )
    with pytest.raises(ha_bundle.BundleError, match="reserved"):
        ha_bundle.validate_public_host("duckdock.example.com")
    with pytest.raises(ha_bundle.BundleError, match="too broad"):
        ha_bundle.validate_egress_cidrs(["0.0.0.0/0"])
    with pytest.raises(ha_bundle.BundleError, match="at least 10Gi"):
        ha_bundle.validate_storage_size("9Gi")
    with pytest.raises(ha_bundle.BundleError, match="outside"):
        ha_bundle.validate_output_dir(REPO_ROOT / "generated-target")
    assert ha_bundle.validate_output_dir(tmp_path / "bundle") == (tmp_path / "bundle").resolve()


def test_prepare_verify_and_tamper_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ha_bundle, "source_state", lambda: SOURCE.copy())
    output_dir = tmp_path / "ha-target"

    prepared = ha_bundle.prepare_bundle(_args(output_dir))

    assert prepared["status"] == "PREPARED_NOT_AUTHORIZED"
    assert prepared["migration_job"] == "duckdock-migrate-aaaaaaaaaaaa"
    assert prepared["verification"]["resource_count"] == 22
    assert prepared["target"]["egress_cidrs"] == ["10.20.0.0/16", "172.20.0.0/16"]
    assert {item.name for item in output_dir.iterdir()} == {
        "bootstrap.yaml",
        "migration.yaml",
        "applications.yaml",
        "receipt.json",
        "receipt.sha256",
    }
    assert os.stat(output_dir).st_mode & 0o777 == 0o700
    assert all(os.stat(item).st_mode & 0o777 == 0o600 for item in output_dir.iterdir())

    verified = ha_bundle.verify_bundle(output_dir)
    assert verified["status"] == "TARGET_HA_BUNDLE_VERIFIED_NOT_AUTHORIZED"
    receipt_path = output_dir / "receipt.json"
    sidecar_path = output_dir / "receipt.sha256"
    original_receipt = receipt_path.read_text(encoding="utf-8")
    original_sidecar = sidecar_path.read_text(encoding="utf-8")
    receipt = json.loads(original_receipt)
    receipt["external_requirements"].pop("fault_domain_exercise")
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sidecar_path.write_text(
        f"{ha_bundle.sha256_file(receipt_path)}  receipt.json\n", encoding="utf-8"
    )
    with pytest.raises(ha_bundle.BundleError, match="external requirements changed"):
        ha_bundle.verify_bundle(output_dir)
    receipt_path.write_text(original_receipt, encoding="utf-8")
    sidecar_path.write_text(original_sidecar, encoding="utf-8")

    phases = {
        phase: ha_bundle._load_phase(output_dir / phase)
        for phase in ha_bundle.PHASE_FILES
    }
    assert [item["kind"] for item in phases["migration.yaml"]] == ["Job"]
    assert {item["kind"] for item in phases["bootstrap.yaml"]} == {
        "Namespace",
        "ServiceAccount",
        "PersistentVolumeClaim",
        "NetworkPolicy",
    }
    assert all(
        item["kind"] in ha_bundle.PHASE_FILES["applications.yaml"]
        for item in phases["applications.yaml"]
    )

    beat = next(
        item
        for item in phases["applications.yaml"]
        if item.get("kind") == "Deployment" and item["metadata"]["name"] == "beat"
    )
    beat["spec"]["replicas"] = 3
    with pytest.raises(ha_bundle.BundleError, match="singleton"):
        ha_bundle.verify_documents(
            phases,
            source=verified["source"],
            target=verified["target"],
            migration_name=verified["migration_job"],
        )

    extra = output_dir / "unplanned.txt"
    extra.write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(ha_bundle.BundleError, match="unexpected entries"):
        ha_bundle.verify_bundle(output_dir)


def test_bundle_directory_symlink_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ha_bundle, "source_state", lambda: SOURCE.copy())
    output_dir = tmp_path / "ha-target"
    ha_bundle.prepare_bundle(_args(output_dir))
    link = tmp_path / "ha-target-link"
    link.symlink_to(output_dir, target_is_directory=True)

    with pytest.raises(ha_bundle.BundleError, match="symbolic link"):
        ha_bundle.verify_bundle(link)
