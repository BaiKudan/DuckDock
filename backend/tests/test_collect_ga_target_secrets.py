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

import scripts.collect_ga_target_secrets as target_secrets
from scripts.ga_release_identity import build_release_binding


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
TARGET = "customer-production"
EXERCISE_ID = "ga-secrets-20260806"
PROVIDER = "External Secrets"
PROVIDER_IDENTITY = "secret-provider@example.com"
VERIFIER_IDENTITY = "secret-verifier@example.com"


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


def _policy(tmp_path: Path, *, reused_key: bool = False) -> tuple[Path, Path, Path]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed secret receipt tests")
    provider_key = tmp_path / "provider_key"
    verifier_key = tmp_path / "verifier_key"
    _generate_key(provider_key)
    if reused_key:
        verifier_public = provider_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    else:
        _generate_key(verifier_key)
        verifier_public = verifier_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    provider_public = provider_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    trust = tmp_path / "secrets_allowed_signers"
    trust.write_text(
        f"{PROVIDER_IDENTITY} {provider_public}\n{VERIFIER_IDENTITY} {verifier_public}\n",
        encoding="utf-8",
    )
    policy_path = tmp_path / "secrets-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": target_secrets.POLICY_SCHEMA_VERSION,
                "policy_id": "duckdock-target-secrets-authority",
                "organization": "DuckDock Test Security",
                "allowed_signers_path": str(trust),
                "allowed_signers_sha256": hashlib.sha256(trust.read_bytes()).hexdigest(),
                "approved_providers": [PROVIDER],
                "provider_identities": [PROVIDER_IDENTITY],
                "verifier_identities": [VERIFIER_IDENTITY],
                "required_secret_classes": list(target_secrets.REQUIRED_SECRET_CLASSES),
                "workload_components": list(target_secrets.WORKLOAD_COMPONENTS),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return policy_path, provider_key, verifier_key


def _binding() -> dict:
    return build_release_binding(
        scope="target-production",
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
    )


def _args(tmp_path: Path, policy_path: Path) -> SimpleNamespace:
    rotation = tmp_path / "rotation.json"
    verification = tmp_path / "verification.json"
    return SimpleNamespace(
        context="target-context",
        namespace="duckdock",
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        provider=PROVIDER,
        secret_name="duckdock-runtime-secrets",
        exercise_id=EXERCISE_ID,
        secrets_policy=policy_path,
        provider_signer_identity=PROVIDER_IDENTITY,
        verifier_signer_identity=VERIFIER_IDENTITY,
        rotation_receipt=rotation,
        rotation_signature=Path(f"{rotation}.sig"),
        verification_receipt=verification,
        verification_signature=Path(f"{verification}.sig"),
        receipt_timeout=1.0,
        rollout_timeout=1.0,
        output=tmp_path / "secrets-evidence.json",
        release_binding=_binding(),
    )


def _rotation_receipt(now: datetime) -> dict:
    started = now - timedelta(seconds=2)
    rotated = now - timedelta(seconds=1)
    return {
        "schema_version": target_secrets.ROTATION_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "target_environment": TARGET,
        "provider": PROVIDER,
        "secret_name": "duckdock-runtime-secrets",
        "started_at": started.isoformat(),
        "completed_at": now.isoformat(),
        "encrypted_at_rest": True,
        "access_audit_enabled": True,
        "credentials_external_to_evidence": True,
        "secret_classes": [
            {
                "name": name,
                "previous_version_id": f"{name}-version-41",
                "new_version_id": f"{name}-version-42",
                "provider_receipt_id": f"provider-{name}-42",
                "audit_event_id": f"audit-rotate-{name}-42",
                "rotated_at": rotated.isoformat(),
                "old_version_disabled_at": now.isoformat(),
            }
            for name in target_secrets.REQUIRED_SECRET_CLASSES
        ],
    }


def _verification_receipt(now: datetime) -> dict:
    return {
        "schema_version": target_secrets.VERIFICATION_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "target_environment": TARGET,
        "secret_name": "duckdock-runtime-secrets",
        "started_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "all_passed": True,
        "secret_classes": [
            {
                "name": name,
                "probe_receipt_id": f"probe-{name}-42",
                "tested_at": now.isoformat(),
                "previous_version_rejected": True,
                "new_version_accepted": True,
                "audit_event_id": f"audit-probe-{name}-42",
            }
            for name in target_secrets.REQUIRED_SECRET_CLASSES
        ],
    }


def _write_receipts(
    args: SimpleNamespace,
    provider_key: Path,
    verifier_key: Path,
    *,
    verification_key: Path | None = None,
) -> None:
    time.sleep(0.03)
    rotation_at = datetime.now(timezone.utc)
    args.rotation_receipt.write_text(
        json.dumps(_rotation_receipt(rotation_at), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _sign(
        args.rotation_receipt,
        provider_key,
        target_secrets.ROTATION_SIGNATURE_NAMESPACE,
    )
    verification_at = datetime.now(timezone.utc)
    args.verification_receipt.write_text(
        json.dumps(_verification_receipt(verification_at), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _sign(
        args.verification_receipt,
        verification_key or verifier_key,
        target_secrets.VERIFICATION_SIGNATURE_NAMESPACE,
    )


class FakeKubectl:
    def __init__(self, *, advance_generation: bool = True) -> None:
        self.secret_reads = 0
        self.advance_generation = advance_generation
        self.commands: list[list[str]] = []

    def text(
        self,
        arguments: list[str],
        *,
        namespaced: bool = False,
        timeout: float = 600,
    ) -> str:
        del namespaced, timeout
        self.commands.append(list(arguments))
        if arguments[:2] == ["config", "get-contexts"]:
            return "target-context"
        if arguments[:3] == ["auth", "can-i", "get"]:
            return "yes"
        if arguments[:2] == ["get", "deployment"]:
            component = arguments[2]
            after = self.secret_reads > 0
            generation = 5 if after and self.advance_generation else 4
            resource_version = "25" if after else "20"
            replicas = {"backend": 3, "worker": 3, "beat": 1}[component]
            return "\t".join(
                [
                    component,
                    f"uid-deployment-{component}",
                    resource_version,
                    str(generation),
                    str(replicas),
                    str(generation),
                    str(replicas),
                    str(replicas),
                    str(replicas),
                ]
            )
        if arguments[:2] == ["get", "pods"]:
            selector = arguments[arguments.index("-l") + 1]
            component = selector.rsplit("=", maxsplit=1)[1]
            after = self.secret_reads > 0
            replicas = {"backend": 3, "worker": 3, "beat": 1}[component]
            revision = "new" if after else "old"
            return "\n".join(
                "\t".join(
                    [
                        f"{component}-{revision}-{index}",
                        f"uid-{component}-{revision}-{index}",
                        "30" if after else "21",
                        "2026-08-06T00:00:00Z",
                        "",
                        "Running",
                        "True",
                    ]
                )
                for index in range(1, replicas + 1)
            )
        if arguments[:2] == ["get", "secret"]:
            after = self.secret_reads > 0
            self.secret_reads += 1
            return "\t".join(
                [
                    "duckdock-runtime-secrets",
                    "uid-runtime-secrets",
                    "11" if after else "10",
                    "2026-08-06T00:00:00Z",
                ]
            )
        raise AssertionError(f"unexpected kubectl call: {arguments}")


def test_collects_signed_metadata_only_target_rotation(tmp_path: Path) -> None:
    policy_path, provider_key, verifier_key = _policy(tmp_path)
    args = _args(tmp_path, policy_path)
    runner = FakeKubectl()
    writer = threading.Thread(
        target=_write_receipts,
        args=(args, provider_key, verifier_key),
        daemon=True,
    )
    writer.start()

    report = target_secrets.collect(args, kubectl=runner)
    writer.join(timeout=1)

    assert report["status"] == "PASS"
    assert report["kubernetes_observation"]["metadata_only"] is True
    assert report["kubernetes_observation"]["secret_data_read"] is False
    assert report["kubernetes_observation"]["verification"][
        "secret_resource_version_changed"
    ] is True
    assert all(
        item["all_old_pods_replaced"] is True
        for item in report["kubernetes_observation"]["verification"]["components"].values()
    )
    secret_commands = [command for command in runner.commands if command[:2] == ["get", "secret"]]
    assert secret_commands
    assert all(any(value.startswith("jsonpath=") for value in command) for command in secret_commands)
    assert all(".data" not in " ".join(command) for command in secret_commands)
    assert not target_secrets._contains_secret_material_key(report)


def test_rejects_verification_receipt_signed_by_provider(tmp_path: Path) -> None:
    policy_path, provider_key, verifier_key = _policy(tmp_path)
    args = _args(tmp_path, policy_path)
    args.receipt_timeout = 0.1
    writer = threading.Thread(
        target=_write_receipts,
        args=(args, provider_key, verifier_key),
        kwargs={"verification_key": provider_key},
        daemon=True,
    )
    writer.start()

    with pytest.raises(RuntimeError, match="timed out waiting for signed secrets receipt"):
        target_secrets.collect(args, kubectl=FakeKubectl())
    writer.join(timeout=1)


def test_rejects_reused_key_across_provider_and_verifier(tmp_path: Path) -> None:
    policy_path, _, _ = _policy(tmp_path, reused_key=True)

    with pytest.raises(ValueError, match="reuses a public key"):
        target_secrets.load_secrets_policy(
            policy_path,
            provider=PROVIDER,
            provider_identity=PROVIDER_IDENTITY,
            verifier_identity=VERIFIER_IDENTITY,
        )


def test_requires_workload_generation_advance(tmp_path: Path) -> None:
    policy_path, provider_key, verifier_key = _policy(tmp_path)
    args = _args(tmp_path, policy_path)
    args.rollout_timeout = 0.1
    writer = threading.Thread(
        target=_write_receipts,
        args=(args, provider_key, verifier_key),
        daemon=True,
    )
    writer.start()

    with pytest.raises(RuntimeError, match="timed out waiting for target workload reload"):
        target_secrets.collect(
            args,
            kubectl=FakeKubectl(advance_generation=False),
        )
    writer.join(timeout=1)


def test_rejects_existing_receipt_paths_as_replay(tmp_path: Path) -> None:
    policy_path, _, _ = _policy(tmp_path)
    args = _args(tmp_path, policy_path)
    args.rotation_receipt.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must not exist before"):
        target_secrets.collect(args, kubectl=FakeKubectl())


def test_rejects_duplicate_provider_audit_event_ids() -> None:
    receipt = _rotation_receipt(datetime.now(timezone.utc))
    receipt["secret_classes"][1]["audit_event_id"] = receipt["secret_classes"][0][
        "audit_event_id"
    ]

    with pytest.raises(ValueError, match="invalid signed secret rotation receipt"):
        target_secrets.validate_rotation_receipt(
            receipt,
            exercise_id=EXERCISE_ID,
            target_environment=TARGET,
            provider=PROVIDER,
            secret_name="duckdock-runtime-secrets",
        )
