#!/usr/bin/env python3
"""Collect signed offsite-media and non-production recovery evidence.

The collector never receives decryption keys, database passwords, or object
store credentials.  It verifies the release-bound backup manifest, then waits
for three exact-principal OpenSSH-signed receipts: immutable offsite media,
destructive restore execution in a non-production target, and independent
post-restore integrity verification.  RPO/RTO are derived from the signed
timeline rather than accepted as operator-entered values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

try:
    from scripts.ga_release_identity import build_release_binding
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_identity import build_release_binding


SCHEMA_VERSION = "duckdock-ga-recovery-evidence-v2"
POLICY_SCHEMA_VERSION = "duckdock-ga-recovery-trust-policy-v1"
MEDIA_SCHEMA_VERSION = "duckdock-ga-backup-media-receipt-v1"
RESTORE_SCHEMA_VERSION = "duckdock-ga-restore-execution-receipt-v1"
VERIFICATION_SCHEMA_VERSION = "duckdock-ga-recovery-verification-receipt-v1"
MEDIA_SIGNATURE_NAMESPACE = "duckdock-backup-media-receipt"
RESTORE_SIGNATURE_NAMESPACE = "duckdock-restore-execution-receipt"
VERIFICATION_SIGNATURE_NAMESPACE = "duckdock-recovery-verification-receipt"
BACKUP_SIGNATURE_NAMESPACE = "duckdock-backup"
BACKUP_SCHEMA_VERSION = "duckdock-secure-backup-v1"
REQUIRED_ARTIFACTS = ("minio.tar.gz.age", "mysql.sql.gz.age", "repos.tar.gz.age")
REQUIRED_STAGES = ("mysql", "repositories", "object-store")
RESTORE_ENVIRONMENT_CLASSES = {"recovery", "staging"}
MINIMUM_RETENTION = timedelta(days=30)
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
        "client_secret",
        "credential",
        "credential_value",
        "decryption_key",
        "password",
        "password_value",
        "plaintext",
        "plaintext_value",
        "private_key",
        "raw_secret",
        "secret_value",
        "secret_values",
        "session_token",
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
        raise ValueError(f"cannot read recovery allowed-signers: {exc}") from exc
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
                f"recovery allowed-signers line {line_number} has no OpenSSH public key"
            )
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not _meaningful(principal) or any(marker in principal for marker in "*?!"):
                raise ValueError(
                    f"recovery allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("recovery allowed-signers contains no principals")
    owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            owners.setdefault(key, set()).add(principal)
    if any(len(principals) != 1 for principals in owners.values()):
        raise ValueError("recovery allowed-signers reuses a public key across identities")
    return bindings


def load_recovery_policy(
    path: Path,
    *,
    storage_provider: str,
    storage_identity: str,
    restore_identity: str,
    verifier_identity: str,
) -> tuple[dict[str, Any], Path, str]:
    policy = _load_object(path, "recovery trust policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "approved_storage_providers",
        "storage_identities",
        "restore_executor_identities",
        "verification_identities",
        "required_artifacts",
        "required_restore_stages",
        "minimum_retention_days",
    }

    def exact_list(value: Any) -> list[str] | None:
        if not (
            isinstance(value, list)
            and bool(value)
            and all(_meaningful(item) for item in value)
            and len(value) == len(set(value))
        ):
            return None
        return [str(item) for item in value]

    providers = exact_list(policy.get("approved_storage_providers"))
    storage_identities = exact_list(policy.get("storage_identities"))
    restore_identities = exact_list(policy.get("restore_executor_identities"))
    verification_identities = exact_list(policy.get("verification_identities"))
    identity_sets = [
        set(value or [])
        for value in (storage_identities, restore_identities, verification_identities)
    ]
    identities_disjoint = all(
        not identity_sets[left].intersection(identity_sets[right])
        for left in range(len(identity_sets))
        for right in range(left + 1, len(identity_sets))
    )
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == POLICY_SCHEMA_VERSION
        and _meaningful(policy.get("policy_id"))
        and _meaningful(policy.get("organization"))
        and providers is not None
        and storage_provider in providers
        and storage_identities is not None
        and storage_identity in storage_identities
        and restore_identities is not None
        and restore_identity in restore_identities
        and verification_identities is not None
        and verifier_identity in verification_identities
        and identities_disjoint
        and len({storage_identity, restore_identity, verifier_identity}) == 3
        and policy.get("required_artifacts") == list(REQUIRED_ARTIFACTS)
        and policy.get("required_restore_stages") == list(REQUIRED_STAGES)
        and policy.get("minimum_retention_days") == MINIMUM_RETENTION.days
    ):
        raise ValueError("recovery trust policy does not authorize the requested exercise")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("recovery trust policy has no allowed_signers_path")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_digest = str(policy.get("allowed_signers_sha256", ""))
    if (
        not trust_path.is_file()
        or not DIGEST_RE.fullmatch(expected_digest)
        or _sha256(trust_path) != expected_digest
    ):
        raise ValueError("recovery trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    configured = set().union(*identity_sets)
    if set(bindings) != configured:
        raise ValueError("recovery trust store principals do not exactly match policy identities")
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
        raise ValueError(f"cannot verify signed recovery evidence: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid recovery evidence signature: {detail}")


def validate_backup_manifest(
    path: Path,
    signature: Path,
    *,
    identity: str,
    allowed_signers: Path,
    source_commit: str,
) -> tuple[dict[str, Any], datetime]:
    manifest = _load_object(path, "backup manifest")
    artifacts = manifest.get("artifacts")
    created_at = _parse_time(manifest.get("created_at"))
    if not (
        set(manifest)
        == {"schema_version", "created_at", "timestamp", "release_commit", "encryption", "artifacts"}
        and manifest.get("schema_version") == BACKUP_SCHEMA_VERSION
        and manifest.get("release_commit") == source_commit
        and manifest.get("encryption") == "age-x25519"
        and created_at is not None
        and isinstance(manifest.get("timestamp"), str)
        and bool(re.fullmatch(r"[0-9]{8}-[0-9]{6}", manifest["timestamp"]))
        and isinstance(artifacts, dict)
        and set(artifacts) == set(REQUIRED_ARTIFACTS)
        and all(
            isinstance(artifacts[name], dict)
            and set(artifacts[name])
            == {
                "encrypted_sha256",
                "encrypted_size_bytes",
                "plaintext_sha256",
                "plaintext_size_bytes",
            }
            and bool(DIGEST_RE.fullmatch(str(artifacts[name].get("encrypted_sha256", ""))))
            and bool(DIGEST_RE.fullmatch(str(artifacts[name].get("plaintext_sha256", ""))))
            and isinstance(artifacts[name].get("encrypted_size_bytes"), int)
            and not isinstance(artifacts[name].get("encrypted_size_bytes"), bool)
            and artifacts[name]["encrypted_size_bytes"] > 0
            and isinstance(artifacts[name].get("plaintext_size_bytes"), int)
            and not isinstance(artifacts[name].get("plaintext_size_bytes"), bool)
            and artifacts[name]["plaintext_size_bytes"] > 0
            for name in REQUIRED_ARTIFACTS
        )
        and not _contains_secret_material_key(manifest)
    ):
        raise ValueError("backup manifest is not a complete release-bound secure backup")
    verify_signature(
        path,
        signature,
        identity=identity,
        allowed_signers=allowed_signers,
        namespace=BACKUP_SIGNATURE_NAMESPACE,
    )
    return manifest, created_at


def _safe_offsite_uri(value: Any) -> bool:
    if not _meaningful(value):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "s3"
        and bool(parsed.netloc)
        and bool(parsed.path.strip("/"))
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and "@" not in value
    )


def validate_media_receipt(
    receipt: dict[str, Any],
    *,
    exercise_id: str,
    target_environment: str,
    storage_provider: str,
    manifest_sha256: str,
) -> datetime:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "storage_provider",
        "backup_manifest_sha256",
        "backup_set_id",
        "remote_uri",
        "recovery_point_at",
        "artifact_versions",
        "object_lock",
        "completed_at",
    }
    completed = _parse_time(receipt.get("completed_at"))
    recovery_point = _parse_time(receipt.get("recovery_point_at"))
    artifacts = receipt.get("artifact_versions")
    lock = receipt.get("object_lock")
    retention_until = (
        _parse_time(lock.get("retention_until")) if isinstance(lock, dict) else None
    )
    observed_at = _parse_time(lock.get("observed_at")) if isinstance(lock, dict) else None
    artifact_retention = [
        _parse_time(item.get("retained_until"))
        for item in artifacts or []
        if isinstance(item, dict)
    ]
    artifact_names = [
        item.get("name") for item in artifacts or [] if isinstance(item, dict)
    ]
    version_ids = [
        item.get("version_id") for item in artifacts or [] if isinstance(item, dict)
    ]
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == MEDIA_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("target_environment") == target_environment
        and receipt.get("storage_provider") == storage_provider
        and receipt.get("backup_manifest_sha256") == manifest_sha256
        and _meaningful(receipt.get("backup_set_id"))
        and _safe_offsite_uri(receipt.get("remote_uri"))
        and recovery_point is not None
        and completed is not None
        and recovery_point <= completed
        and isinstance(artifacts, list)
        and len(artifacts) == len(REQUIRED_ARTIFACTS)
        and artifact_names == list(REQUIRED_ARTIFACTS)
        and len(version_ids) == len(set(version_ids))
        and all(
            isinstance(item, dict)
            and set(item)
            == {"name", "version_id", "etag", "sha256", "size_bytes", "retained_until"}
            and _meaningful(item.get("version_id"))
            and _meaningful(item.get("etag"))
            and bool(DIGEST_RE.fullmatch(str(item.get("sha256", ""))))
            and isinstance(item.get("size_bytes"), int)
            and not isinstance(item.get("size_bytes"), bool)
            and item["size_bytes"] > 0
            for item in artifacts
        )
        and len(artifact_retention) == len(REQUIRED_ARTIFACTS)
        and all(value is not None for value in artifact_retention)
        and isinstance(lock, dict)
        and set(lock)
        == {"enabled", "mode", "retention_until", "provider_receipt_id", "observed_at"}
        and lock.get("enabled") is True
        and lock.get("mode") in {"COMPLIANCE", "GOVERNANCE"}
        and _meaningful(lock.get("provider_receipt_id"))
        and observed_at is not None
        and retention_until is not None
        and observed_at <= completed
        and retention_until >= completed + MINIMUM_RETENTION
        and all(value >= retention_until for value in artifact_retention if value is not None)
        and not _contains_secret_material_key(receipt)
    ):
        raise ValueError("invalid signed offsite backup media receipt")
    return completed


def validate_restore_receipt(
    receipt: dict[str, Any],
    *,
    exercise_id: str,
    production_target_environment: str,
    restore_target_environment: str,
    manifest_sha256: str,
) -> datetime:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "production_target_environment",
        "restore_target_environment",
        "environment_class",
        "backup_manifest_sha256",
        "restore_receipt_id",
        "destructive_restore",
        "production_data_overwrite",
        "failure_injected_at",
        "started_at",
        "completed_at",
        "stages",
    }
    failure_at = _parse_time(receipt.get("failure_injected_at"))
    started = _parse_time(receipt.get("started_at"))
    completed = _parse_time(receipt.get("completed_at"))
    stages = receipt.get("stages")
    stage_names = [item.get("name") for item in stages or [] if isinstance(item, dict)]
    stage_times = [
        _parse_time(item.get("completed_at"))
        for item in stages or []
        if isinstance(item, dict)
    ]
    command_ids = [
        item.get("command_id") for item in stages or [] if isinstance(item, dict)
    ]
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == RESTORE_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("production_target_environment") == production_target_environment
        and receipt.get("restore_target_environment") == restore_target_environment
        and restore_target_environment != production_target_environment
        and receipt.get("environment_class") in RESTORE_ENVIRONMENT_CLASSES
        and receipt.get("backup_manifest_sha256") == manifest_sha256
        and _meaningful(receipt.get("restore_receipt_id"))
        and receipt.get("destructive_restore") is True
        and receipt.get("production_data_overwrite") is False
        and failure_at is not None
        and started is not None
        and completed is not None
        and failure_at <= started <= completed
        and isinstance(stages, list)
        and len(stages) == len(REQUIRED_STAGES)
        and stage_names == list(REQUIRED_STAGES)
        and len(command_ids) == len(set(command_ids))
        and all(
            isinstance(item, dict)
            and set(item) == {"name", "command_id", "exit_code", "log_sha256", "completed_at"}
            and _meaningful(item.get("command_id"))
            and item.get("exit_code") == 0
            and bool(DIGEST_RE.fullmatch(str(item.get("log_sha256", ""))))
            for item in stages
        )
        and len(stage_times) == len(REQUIRED_STAGES)
        and all(value is not None and started <= value <= completed for value in stage_times)
        and stage_times == sorted(stage_times)
        and not _contains_secret_material_key(receipt)
    ):
        raise ValueError("invalid signed non-production restore execution receipt")
    return completed


def validate_verification_receipt(
    receipt: dict[str, Any],
    *,
    exercise_id: str,
    restore_target_environment: str,
    manifest_sha256: str,
) -> datetime:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "restore_target_environment",
        "environment_class",
        "backup_manifest_sha256",
        "verification_receipt_id",
        "started_at",
        "completed_at",
        "mysql",
        "object_store",
        "git",
        "service_readiness",
        "passed",
    }
    started = _parse_time(receipt.get("started_at"))
    completed = _parse_time(receipt.get("completed_at"))
    mysql = receipt.get("mysql")
    object_store = receipt.get("object_store")
    git = receipt.get("git")
    readiness = receipt.get("service_readiness")
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == VERIFICATION_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("restore_target_environment") == restore_target_environment
        and receipt.get("environment_class") in RESTORE_ENVIRONMENT_CLASSES
        and receipt.get("backup_manifest_sha256") == manifest_sha256
        and _meaningful(receipt.get("verification_receipt_id"))
        and started is not None
        and completed is not None
        and started <= completed
        and isinstance(mysql, dict)
        and set(mysql) == {"rows_verified", "dataset_sha256"}
        and isinstance(mysql.get("rows_verified"), int)
        and not isinstance(mysql.get("rows_verified"), bool)
        and mysql["rows_verified"] > 0
        and bool(DIGEST_RE.fullmatch(str(mysql.get("dataset_sha256", ""))))
        and isinstance(object_store, dict)
        and set(object_store) == {"objects_verified", "inventory_sha256"}
        and isinstance(object_store.get("objects_verified"), int)
        and not isinstance(object_store.get("objects_verified"), bool)
        and object_store["objects_verified"] > 0
        and bool(DIGEST_RE.fullmatch(str(object_store.get("inventory_sha256", ""))))
        and isinstance(git, dict)
        and set(git) == {"repositories_verified", "refs_sha256"}
        and isinstance(git.get("repositories_verified"), int)
        and not isinstance(git.get("repositories_verified"), bool)
        and git["repositories_verified"] > 0
        and bool(DIGEST_RE.fullmatch(str(git.get("refs_sha256", ""))))
        and isinstance(readiness, dict)
        and set(readiness) == {"https_probe_passed", "background_worker_ready"}
        and readiness.get("https_probe_passed") is True
        and readiness.get("background_worker_ready") is True
        and receipt.get("passed") is True
        and not _contains_secret_material_key(receipt)
    ):
        raise ValueError("invalid signed independent recovery verification receipt")
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
                receipt = _load_object(path, "signed recovery receipt")
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
    raise RuntimeError(f"timed out waiting for signed recovery receipt: {last_error}")


def _signed_reference(path: Path, signature: Path, *, identity: str) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "signature_path": str(signature.resolve()),
        "signer_identity": identity,
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    receipt_paths = (
        args.media_receipt,
        args.media_signature,
        args.restore_receipt,
        args.restore_signature,
        args.verification_receipt,
        args.verification_signature,
    )
    if any(path.exists() for path in receipt_paths):
        raise ValueError("recovery receipt/signature paths must not exist before the exercise")
    policy, allowed_signers, policy_digest = load_recovery_policy(
        args.recovery_policy,
        storage_provider=args.storage_provider,
        storage_identity=args.storage_signer_identity,
        restore_identity=args.restore_signer_identity,
        verifier_identity=args.verifier_signer_identity,
    )
    manifest, manifest_created_at = validate_backup_manifest(
        args.backup_manifest,
        args.backup_signature,
        identity=args.backup_signer_identity,
        allowed_signers=args.backup_allowed_signers,
        source_commit=args.source_commit,
    )
    manifest_digest = _sha256(args.backup_manifest)
    collection_started = datetime.now(timezone.utc)
    print(
        f"Backup manifest verified for {args.target_environment}; begin destructive restore "
        f"exercise {args.exercise_id} in non-production target "
        f"{args.restore_target_environment} now.",
        file=sys.stderr,
        flush=True,
    )
    media, media_completed = wait_signed_receipt(
        args.media_receipt,
        args.media_signature,
        identity=args.storage_signer_identity,
        allowed_signers=allowed_signers,
        namespace=MEDIA_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_media_receipt(
            receipt,
            exercise_id=args.exercise_id,
            target_environment=args.target_environment,
            storage_provider=args.storage_provider,
            manifest_sha256=manifest_digest,
        ),
    )
    media_artifacts = {
        item["name"]: item for item in media["artifact_versions"]
    }
    if any(
        media_artifacts[name]["sha256"]
        != manifest["artifacts"][name]["encrypted_sha256"]
        or media_artifacts[name]["size_bytes"]
        != manifest["artifacts"][name]["encrypted_size_bytes"]
        for name in REQUIRED_ARTIFACTS
    ):
        raise ValueError("offsite backup versions do not match the signed manifest artifacts")
    restore, restore_completed = wait_signed_receipt(
        args.restore_receipt,
        args.restore_signature,
        identity=args.restore_signer_identity,
        allowed_signers=allowed_signers,
        namespace=RESTORE_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_restore_receipt(
            receipt,
            exercise_id=args.exercise_id,
            production_target_environment=args.target_environment,
            restore_target_environment=args.restore_target_environment,
            manifest_sha256=manifest_digest,
        ),
    )
    verification, verification_completed = wait_signed_receipt(
        args.verification_receipt,
        args.verification_signature,
        identity=args.verifier_signer_identity,
        allowed_signers=allowed_signers,
        namespace=VERIFICATION_SIGNATURE_NAMESPACE,
        timeout_seconds=args.receipt_timeout,
        validator=lambda receipt: validate_verification_receipt(
            receipt,
            exercise_id=args.exercise_id,
            restore_target_environment=args.restore_target_environment,
            manifest_sha256=manifest_digest,
        ),
    )
    recovery_point = _parse_time(media.get("recovery_point_at"))
    failure_at = _parse_time(restore.get("failure_injected_at"))
    restore_started = _parse_time(restore.get("started_at"))
    verification_started = _parse_time(verification.get("started_at"))
    if not all(
        value is not None
        for value in (recovery_point, failure_at, restore_started, verification_started)
    ):
        raise ValueError("recovery receipt timeline is incomplete")
    if not (
        recovery_point <= manifest_created_at <= media_completed
        and recovery_point <= failure_at <= restore_started <= restore_completed
        and restore_completed <= verification_started <= verification_completed
        and media_completed >= collection_started - timedelta(minutes=5)
        and restore_completed >= collection_started - timedelta(minutes=5)
        and verification_completed >= collection_started - timedelta(minutes=5)
    ):
        raise ValueError("signed recovery receipts are stale or out of exercise order")
    observed_at = datetime.now(timezone.utc)
    if verification_completed > observed_at + timedelta(minutes=5):
        raise ValueError("signed recovery receipt completion time is in the future")
    rpo_seconds = math.ceil((failure_at - recovery_point).total_seconds())
    rto_seconds = math.ceil((verification_completed - failure_at).total_seconds())
    mysql_rows = verification["mysql"]["rows_verified"]
    objects = verification["object_store"]["objects_verified"]
    git_repositories = verification["git"]["repositories_verified"]
    passed = (
        rpo_seconds >= 0
        and rpo_seconds <= args.maximum_rpo_seconds
        and rto_seconds >= 0
        and rto_seconds <= args.maximum_rto_seconds
        and mysql_rows > 0
        and objects > 0
        and git_repositories > 0
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASSED" if passed else "BLOCKED",
        "passed": passed,
        "observed_at": observed_at.isoformat(),
        "offsite_media": True,
        "encrypted": manifest["encryption"] == "age-x25519",
        "immutable_or_object_locked": media["object_lock"]["enabled"],
        "storage_provider": args.storage_provider,
        "rpo_seconds": rpo_seconds,
        "rto_seconds": rto_seconds,
        "mysql_rows_verified": mysql_rows,
        "objects_verified": objects,
        "git_repositories_verified": git_repositories > 0,
        "exercise": {
            "exercise_id": args.exercise_id,
            "production_target_environment": args.target_environment,
            "restore_target_environment": args.restore_target_environment,
            "started_at": collection_started.isoformat(),
            "completed_at": observed_at.isoformat(),
            "maximum_rpo_seconds": args.maximum_rpo_seconds,
            "maximum_rto_seconds": args.maximum_rto_seconds,
        },
        "backup": {
            "manifest_schema_version": BACKUP_SCHEMA_VERSION,
            "path": str(args.backup_manifest.resolve()),
            "manifest_sha256": manifest_digest,
            "signer_identity": args.backup_signer_identity,
            "allowed_signers_path": str(args.backup_allowed_signers.resolve()),
            "allowed_signers_sha256": _sha256(args.backup_allowed_signers),
            "signature_path": str(args.backup_signature.resolve()),
            "decryption_key_external": True,
        },
        "recovery_policy": {
            "path": str(args.recovery_policy.resolve()),
            "sha256": policy_digest,
            "policy_id": policy["policy_id"],
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": _sha256(allowed_signers),
        },
        "media_receipt": {
            **media,
            "signed_evidence": _signed_reference(
                args.media_receipt,
                args.media_signature,
                identity=args.storage_signer_identity,
            ),
        },
        "restore_receipt": {
            **restore,
            "signed_evidence": _signed_reference(
                args.restore_receipt,
                args.restore_signature,
                identity=args.restore_signer_identity,
            ),
        },
        "verification_receipt": {
            **verification,
            "signed_evidence": _signed_reference(
                args.verification_receipt,
                args.verification_signature,
                identity=args.verifier_signer_identity,
            ),
        },
    }
    if _contains_secret_material_key(report):
        raise ValueError("recovery report contains a forbidden secret-material field")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--restore-target-environment", required=True)
    parser.add_argument("--acknowledge-destructive-restore", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--maximum-rpo-seconds", type=int, required=True)
    parser.add_argument("--maximum-rto-seconds", type=int, required=True)
    parser.add_argument("--storage-provider", required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--backup-signature", type=Path, required=True)
    parser.add_argument("--backup-allowed-signers", type=Path, required=True)
    parser.add_argument("--backup-signer-identity", required=True)
    parser.add_argument("--recovery-policy", type=Path, required=True)
    parser.add_argument("--storage-signer-identity", required=True)
    parser.add_argument("--restore-signer-identity", required=True)
    parser.add_argument("--verifier-signer-identity", required=True)
    parser.add_argument("--media-receipt", type=Path, required=True)
    parser.add_argument("--media-signature", type=Path, required=True)
    parser.add_argument("--restore-receipt", type=Path, required=True)
    parser.add_argument("--restore-signature", type=Path, required=True)
    parser.add_argument("--verification-receipt", type=Path, required=True)
    parser.add_argument("--verification-signature", type=Path, required=True)
    parser.add_argument("--receipt-timeout", type=float, default=1800.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.target_environment == args.restore_target_environment:
            raise ValueError("restore target must differ from production target environment")
        if args.acknowledge_destructive_restore != args.restore_target_environment:
            raise ValueError(
                "--acknowledge-destructive-restore must exactly equal restore target environment"
            )
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe, non-placeholder characters")
        if not all(
            _meaningful(value)
            for value in (
                args.target_environment,
                args.restore_target_environment,
                args.storage_provider,
                args.backup_signer_identity,
                args.storage_signer_identity,
                args.restore_signer_identity,
                args.verifier_signer_identity,
            )
        ):
            raise ValueError("target, provider and signer identities must be non-placeholder")
        if len(
            {
                args.storage_signer_identity,
                args.restore_signer_identity,
                args.verifier_signer_identity,
            }
        ) != 3:
            raise ValueError("storage, restore and verifier signer identities must be distinct")
        if min(
            args.maximum_rpo_seconds,
            args.maximum_rto_seconds,
            args.receipt_timeout,
        ) <= 0:
            raise ValueError("RPO/RTO limits and receipt timeout must be positive")
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new path or pass --overwrite-output")
        for path, label in (
            (args.backup_manifest, "backup manifest"),
            (args.backup_signature, "backup signature"),
            (args.backup_allowed_signers, "backup allowed-signers"),
            (args.recovery_policy, "recovery policy"),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist")
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
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"Target recovery collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
