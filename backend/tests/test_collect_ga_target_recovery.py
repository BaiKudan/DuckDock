from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_target_recovery as target_recovery
from scripts.ga_release_identity import build_release_binding


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
TARGET = "customer-production"
RESTORE_TARGET = "customer-recovery-staging"
EXERCISE_ID = "ga-recovery-20260806"
STORAGE_PROVIDER = "AWS S3"
BACKUP_IDENTITY = "backup-operator@example.com"
STORAGE_IDENTITY = "backup-storage@example.com"
RESTORE_IDENTITY = "restore-executor@example.com"
VERIFIER_IDENTITY = "recovery-verifier@example.com"


def _generate_key(path: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        check=True,
    )


def _sign(path: Path, key: Path, namespace: str) -> Path:
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", namespace, str(path)],
        check=True,
    )
    return Path(f"{path}.sig")


def _write_signed(path: Path, payload: dict, key: Path, namespace: str) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    _sign(path, key, namespace)


def _material(
    tmp_path: Path,
    *,
    reused_role_key: bool = False,
) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed recovery evidence tests")
    backup_key = tmp_path / "backup_key"
    storage_key = tmp_path / "storage_key"
    restore_key = tmp_path / "restore_key"
    verifier_key = tmp_path / "verifier_key"
    for key in (backup_key, storage_key, restore_key):
        _generate_key(key)
    if not reused_role_key:
        _generate_key(verifier_key)

    backup_trust = tmp_path / "backup_allowed_signers"
    backup_public = backup_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    backup_trust.write_text(f"{BACKUP_IDENTITY} {backup_public}\n", encoding="utf-8")

    role_public = {
        STORAGE_IDENTITY: storage_key.with_suffix(".pub").read_text(encoding="utf-8").strip(),
        RESTORE_IDENTITY: restore_key.with_suffix(".pub").read_text(encoding="utf-8").strip(),
        VERIFIER_IDENTITY: (
            restore_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
            if reused_role_key
            else verifier_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        ),
    }
    recovery_trust = tmp_path / "recovery_allowed_signers"
    recovery_trust.write_text(
        "".join(f"{identity} {public}\n" for identity, public in role_public.items()),
        encoding="utf-8",
    )
    policy_path = tmp_path / "recovery-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": target_recovery.POLICY_SCHEMA_VERSION,
                "policy_id": "duckdock-target-recovery-authority",
                "organization": "DuckDock Test Operations",
                "allowed_signers_path": str(recovery_trust),
                "allowed_signers_sha256": hashlib.sha256(
                    recovery_trust.read_bytes()
                ).hexdigest(),
                "approved_storage_providers": [STORAGE_PROVIDER],
                "storage_identities": [STORAGE_IDENTITY],
                "restore_executor_identities": [RESTORE_IDENTITY],
                "verification_identities": [VERIFIER_IDENTITY],
                "required_artifacts": list(target_recovery.REQUIRED_ARTIFACTS),
                "required_restore_stages": list(target_recovery.REQUIRED_STAGES),
                "minimum_retention_days": target_recovery.MINIMUM_RETENTION.days,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": target_recovery.BACKUP_SCHEMA_VERSION,
                "created_at": (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat(),
                "timestamp": "20260806-000000",
                "release_commit": COMMIT,
                "encryption": "age-x25519",
                "artifacts": {
                    name: {
                        "encrypted_sha256": str(index) * 64,
                        "encrypted_size_bytes": 2048 + index,
                        "plaintext_sha256": str(index + 3) * 64,
                        "plaintext_size_bytes": 1024 + index,
                    }
                    for index, name in enumerate(target_recovery.REQUIRED_ARTIFACTS, start=1)
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_signature = _sign(
        manifest_path,
        backup_key,
        target_recovery.BACKUP_SIGNATURE_NAMESPACE,
    )
    return (
        policy_path,
        backup_trust,
        manifest_path,
        manifest_signature,
        storage_key,
        restore_key,
        verifier_key,
    )


def _binding() -> dict:
    return build_release_binding(
        scope="target-production",
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
    )


def _args(
    tmp_path: Path,
    policy_path: Path,
    backup_trust: Path,
    manifest_path: Path,
    manifest_signature: Path,
) -> SimpleNamespace:
    media = tmp_path / "media.json"
    restore = tmp_path / "restore.json"
    verification = tmp_path / "verification.json"
    return SimpleNamespace(
        target_environment=TARGET,
        restore_target_environment=RESTORE_TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        exercise_id=EXERCISE_ID,
        maximum_rpo_seconds=900,
        maximum_rto_seconds=14_400,
        storage_provider=STORAGE_PROVIDER,
        backup_manifest=manifest_path,
        backup_signature=manifest_signature,
        backup_allowed_signers=backup_trust,
        backup_signer_identity=BACKUP_IDENTITY,
        recovery_policy=policy_path,
        storage_signer_identity=STORAGE_IDENTITY,
        restore_signer_identity=RESTORE_IDENTITY,
        verifier_signer_identity=VERIFIER_IDENTITY,
        media_receipt=media,
        media_signature=Path(f"{media}.sig"),
        restore_receipt=restore,
        restore_signature=Path(f"{restore}.sig"),
        verification_receipt=verification,
        verification_signature=Path(f"{verification}.sig"),
        receipt_timeout=1.0,
        output=tmp_path / "target-recovery.json",
        release_binding=_binding(),
    )


def _receipts(manifest_digest: str, base: datetime) -> tuple[dict, dict, dict]:
    recovery_point = base - timedelta(seconds=20)
    retention = base + timedelta(days=40)
    media = {
        "schema_version": target_recovery.MEDIA_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "target_environment": TARGET,
        "storage_provider": STORAGE_PROVIDER,
        "backup_manifest_sha256": manifest_digest,
        "backup_set_id": "backup-set-20260806-001",
        "remote_uri": "s3://duckdock-archive/customer-production/backup-set-20260806-001",
        "recovery_point_at": recovery_point.isoformat(),
        "artifact_versions": [
            {
                "name": name,
                "version_id": f"version-{index}",
                "etag": f"etag-{index}",
                "sha256": str(index) * 64,
                "size_bytes": 2048 + index,
                "retained_until": retention.isoformat(),
            }
            for index, name in enumerate(target_recovery.REQUIRED_ARTIFACTS, start=1)
        ],
        "object_lock": {
            "enabled": True,
            "mode": "COMPLIANCE",
            "retention_until": retention.isoformat(),
            "provider_receipt_id": "storage-lock-receipt-001",
            "observed_at": (base - timedelta(seconds=10)).isoformat(),
        },
        "completed_at": (base - timedelta(seconds=10)).isoformat(),
    }
    failure = base - timedelta(seconds=9)
    restore = {
        "schema_version": target_recovery.RESTORE_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "production_target_environment": TARGET,
        "restore_target_environment": RESTORE_TARGET,
        "environment_class": "recovery",
        "backup_manifest_sha256": manifest_digest,
        "restore_receipt_id": "restore-execution-001",
        "destructive_restore": True,
        "production_data_overwrite": False,
        "failure_injected_at": failure.isoformat(),
        "started_at": (base - timedelta(seconds=8)).isoformat(),
        "completed_at": (base - timedelta(seconds=5)).isoformat(),
        "stages": [
            {
                "name": name,
                "command_id": f"restore-{name}-001",
                "exit_code": 0,
                "log_sha256": str(index + 6) * 64,
                "completed_at": (base - timedelta(seconds=8 - index)).isoformat(),
            }
            for index, name in enumerate(target_recovery.REQUIRED_STAGES, start=1)
        ],
    }
    verification = {
        "schema_version": target_recovery.VERIFICATION_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "restore_target_environment": RESTORE_TARGET,
        "environment_class": "recovery",
        "backup_manifest_sha256": manifest_digest,
        "verification_receipt_id": "recovery-verification-001",
        "started_at": (base - timedelta(seconds=4)).isoformat(),
        "completed_at": (base - timedelta(seconds=1)).isoformat(),
        "mysql": {"rows_verified": 42, "dataset_sha256": "a" * 64},
        "object_store": {"objects_verified": 7, "inventory_sha256": "b" * 64},
        "git": {"repositories_verified": 3, "refs_sha256": "c" * 64},
        "service_readiness": {
            "https_probe_passed": True,
            "background_worker_ready": True,
        },
        "passed": True,
    }
    return media, restore, verification


def _write_receipts(
    args: SimpleNamespace,
    storage_key: Path,
    restore_key: Path,
    verifier_key: Path,
    *,
    verification_key: Path | None = None,
) -> None:
    time.sleep(0.03)
    payloads = _receipts(
        hashlib.sha256(args.backup_manifest.read_bytes()).hexdigest(),
        datetime.now(timezone.utc),
    )
    for path, payload, key, namespace in (
        (
            args.media_receipt,
            payloads[0],
            storage_key,
            target_recovery.MEDIA_SIGNATURE_NAMESPACE,
        ),
        (
            args.restore_receipt,
            payloads[1],
            restore_key,
            target_recovery.RESTORE_SIGNATURE_NAMESPACE,
        ),
        (
            args.verification_receipt,
            payloads[2],
            verification_key or verifier_key,
            target_recovery.VERIFICATION_SIGNATURE_NAMESPACE,
        ),
    ):
        _write_signed(path, payload, key, namespace)


def test_collects_signed_offsite_destructive_recovery_evidence(tmp_path: Path) -> None:
    material = _material(tmp_path)
    args = _args(tmp_path, *material[:4])
    writer = threading.Thread(
        target=_write_receipts,
        args=(args, material[4], material[5], material[6]),
        daemon=True,
    )
    writer.start()

    report = target_recovery.collect(args)
    writer.join(timeout=1)

    assert report["status"] == "PASSED"
    assert report["rpo_seconds"] == 11
    assert report["rto_seconds"] == 8
    assert report["mysql_rows_verified"] == 42
    assert report["objects_verified"] == 7
    assert report["git_repositories_verified"] is True
    assert report["backup"]["decryption_key_external"] is True
    assert not target_recovery._contains_secret_material_key(report)


def test_rejects_verification_signed_by_restore_executor(tmp_path: Path) -> None:
    material = _material(tmp_path)
    args = _args(tmp_path, *material[:4])
    args.receipt_timeout = 0.1
    writer = threading.Thread(
        target=_write_receipts,
        args=(args, material[4], material[5], material[6]),
        kwargs={"verification_key": material[5]},
        daemon=True,
    )
    writer.start()

    with pytest.raises(RuntimeError, match="timed out waiting for signed recovery receipt"):
        target_recovery.collect(args)
    writer.join(timeout=1)


def test_rejects_public_key_reuse_between_recovery_roles(tmp_path: Path) -> None:
    material = _material(tmp_path, reused_role_key=True)

    with pytest.raises(ValueError, match="reuses a public key"):
        target_recovery.load_recovery_policy(
            material[0],
            storage_provider=STORAGE_PROVIDER,
            storage_identity=STORAGE_IDENTITY,
            restore_identity=RESTORE_IDENTITY,
            verifier_identity=VERIFIER_IDENTITY,
        )


def test_rejects_existing_receipt_as_replay(tmp_path: Path) -> None:
    material = _material(tmp_path)
    args = _args(tmp_path, *material[:4])
    args.media_receipt.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must not exist before"):
        target_recovery.collect(args)


def test_reports_block_when_measured_rpo_exceeds_target(tmp_path: Path) -> None:
    material = _material(tmp_path)
    args = _args(tmp_path, *material[:4])
    args.maximum_rpo_seconds = 1
    writer = threading.Thread(
        target=_write_receipts,
        args=(args, material[4], material[5], material[6]),
        daemon=True,
    )
    writer.start()

    report = target_recovery.collect(args)
    writer.join(timeout=1)

    assert report["status"] == "BLOCKED"
    assert report["passed"] is False
    assert report["rpo_seconds"] == 11


def test_rejects_secret_material_field_in_verification_receipt() -> None:
    manifest_digest = "d" * 64
    _, _, receipt = _receipts(manifest_digest, datetime.now(timezone.utc))
    receipt["session_token"] = "must-not-be-retained"

    with pytest.raises(ValueError, match="invalid signed independent recovery"):
        target_recovery.validate_verification_receipt(
            receipt,
            exercise_id=EXERCISE_ID,
            restore_target_environment=RESTORE_TARGET,
            manifest_sha256=manifest_digest,
        )
