"""Shared validation for an immutable DuckDock GA approval campaign freeze."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.ga_path_resolution import ga_file_resolution_override
except ModuleNotFoundError:  # direct script execution
    from ga_path_resolution import ga_file_resolution_override


LEGACY_CAMPAIGN_FREEZE_SCHEMA_VERSION = "duckdock-ga-approval-campaign-freeze-v1"
CAMPAIGN_FREEZE_SCHEMA_VERSION = "duckdock-ga-approval-campaign-freeze-v2"
CAMPAIGN_ID_RE = re.compile(r"^gac_[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
LEGACY_CAMPAIGN_FREEZE_KEYS = {
    "schema_version",
    "campaign_id",
    "frozen_at",
    "approvals_expire_at",
    "authorization",
    "approval_policy",
    "release_digest",
    "evaluation",
}
CAMPAIGN_FREEZE_KEYS = LEGACY_CAMPAIGN_FREEZE_KEYS | {"execution_closure"}
BOUND_FILE_KEYS = {"path", "sha256"}


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def require_formal_campaign_freeze(
    authorization: dict[str, Any],
    *,
    authorization_path: Path,
) -> Path:
    reference = authorization.get("approval_campaign")
    if not isinstance(reference, dict) or set(reference) != {
        "path",
        "sha256",
        "campaign_id",
    }:
        raise ValueError("formal GA authorization has no exact approval campaign reference")
    raw_path = reference.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("formal GA approval campaign path is invalid")
    handled, overridden = ga_file_resolution_override(raw_path)
    if handled:
        if overridden is None:
            raise ValueError("formal GA approval campaign has no portable file mapping")
        freeze_path = overridden
    else:
        freeze_path = Path(raw_path).expanduser()
        if not freeze_path.is_absolute():
            freeze_path = authorization_path.resolve().parent / freeze_path
    if freeze_path.is_symlink() or not freeze_path.is_file():
        raise ValueError("formal GA approval campaign freeze is missing or symlinked")
    freeze_path = freeze_path.resolve()
    if reference.get("sha256") != sha256_path(freeze_path):
        raise ValueError("formal GA approval campaign freeze digest mismatch")
    freeze = load_object(freeze_path, "formal GA approval campaign freeze")
    if freeze.get("schema_version") != CAMPAIGN_FREEZE_SCHEMA_VERSION:
        raise ValueError("formal GA authorization requires a closure-bound campaign freeze v2")
    return freeze_path


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_rfc3339(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an RFC3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not RFC3339: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def campaign_statement(
    *,
    authorization_sha256: str,
    approval_policy_sha256: str,
    release_digest: str,
    frozen_at: str,
    approvals_expire_at: str,
    execution_closure_sha256: str | None = None,
) -> bytes:
    schema_version = (
        CAMPAIGN_FREEZE_SCHEMA_VERSION
        if execution_closure_sha256 is not None
        else LEGACY_CAMPAIGN_FREEZE_SCHEMA_VERSION
    )
    statement = {
        "schema_version": schema_version,
        "authorization_sha256": authorization_sha256,
        "approval_policy_sha256": approval_policy_sha256,
        "release_digest": release_digest,
        "frozen_at": frozen_at,
        "approvals_expire_at": approvals_expire_at,
    }
    if execution_closure_sha256 is not None:
        statement["execution_closure_sha256"] = execution_closure_sha256
    return (
        json.dumps(
            statement,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def derive_campaign_id(
    *,
    authorization_sha256: str,
    approval_policy_sha256: str,
    release_digest: str,
    frozen_at: str,
    approvals_expire_at: str,
    execution_closure_sha256: str | None = None,
) -> str:
    statement = campaign_statement(
        authorization_sha256=authorization_sha256,
        approval_policy_sha256=approval_policy_sha256,
        release_digest=release_digest,
        frozen_at=frozen_at,
        approvals_expire_at=approvals_expire_at,
        execution_closure_sha256=execution_closure_sha256,
    )
    return f"gac_{hashlib.sha256(statement).hexdigest()}"


def validate_campaign_freeze(
    freeze_path: Path,
    *,
    authorization_path: Path,
    approval_policy_path: Path,
    now: datetime,
    require_active: bool = True,
    enforce_recorded_paths: bool = True,
    require_execution_closure: bool = False,
) -> dict[str, Any]:
    freeze = load_object(freeze_path, "approval campaign freeze")
    schema_version = freeze.get("schema_version")
    expected_keys = (
        CAMPAIGN_FREEZE_KEYS
        if schema_version == CAMPAIGN_FREEZE_SCHEMA_VERSION
        else LEGACY_CAMPAIGN_FREEZE_KEYS
    )
    if set(freeze) != expected_keys:
        raise ValueError("approval campaign freeze has invalid fields")
    if schema_version not in {
        CAMPAIGN_FREEZE_SCHEMA_VERSION,
        LEGACY_CAMPAIGN_FREEZE_SCHEMA_VERSION,
    }:
        raise ValueError("approval campaign freeze schema version is unsupported")
    if require_execution_closure and schema_version != CAMPAIGN_FREEZE_SCHEMA_VERSION:
        raise ValueError("formal GA approval requires a closure-bound campaign freeze v2")
    authorization = freeze.get("authorization")
    policy = freeze.get("approval_policy")
    if not isinstance(authorization, dict) or set(authorization) != BOUND_FILE_KEYS:
        raise ValueError("approval campaign freeze authorization binding is invalid")
    if not isinstance(policy, dict) or set(policy) != BOUND_FILE_KEYS:
        raise ValueError("approval campaign freeze policy binding is invalid")

    expected_authorization_path = authorization_path.resolve()
    expected_policy_path = approval_policy_path.resolve()
    try:
        frozen_authorization_path = Path(str(authorization.get("path"))).resolve()
        frozen_policy_path = Path(str(policy.get("path"))).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"approval campaign freeze contains an invalid path: {exc}") from exc
    if enforce_recorded_paths and frozen_authorization_path != expected_authorization_path:
        raise ValueError("authorization path does not match the approval campaign freeze")
    if enforce_recorded_paths and frozen_policy_path != expected_policy_path:
        raise ValueError("approval policy path does not match the approval campaign freeze")

    authorization_digest = sha256_path(expected_authorization_path)
    policy_digest = sha256_path(expected_policy_path)
    if (
        not DIGEST_RE.fullmatch(str(authorization.get("sha256", "")))
        or authorization.get("sha256") != authorization_digest
    ):
        raise ValueError("authorization digest does not match the approval campaign freeze")
    if (
        not DIGEST_RE.fullmatch(str(policy.get("sha256", "")))
        or policy.get("sha256") != policy_digest
    ):
        raise ValueError("approval policy digest does not match the approval campaign freeze")

    release_digest = str(freeze.get("release_digest", ""))
    if not DIGEST_RE.fullmatch(release_digest):
        raise ValueError("approval campaign freeze release digest is invalid")
    frozen_at = parse_rfc3339(freeze.get("frozen_at"), "frozen_at")
    expires_at = parse_rfc3339(freeze.get("approvals_expire_at"), "approvals_expire_at")
    current = now.astimezone(timezone.utc)
    if expires_at <= frozen_at:
        raise ValueError("approval campaign expiry must be after the freeze time")
    if require_active:
        if current < frozen_at:
            raise ValueError("approval campaign freeze time is in the future")
        if current > expires_at:
            raise ValueError("approval campaign has expired")

    closure_validation: dict[str, Any] | None = None
    execution_closure_digest: str | None = None
    if schema_version == CAMPAIGN_FREEZE_SCHEMA_VERSION:
        closure_reference = freeze.get("execution_closure")
        if not isinstance(closure_reference, dict) or set(closure_reference) != BOUND_FILE_KEYS:
            raise ValueError("approval campaign execution closure binding is invalid")
        raw_closure_path = closure_reference.get("path")
        if not isinstance(raw_closure_path, str) or not raw_closure_path.strip():
            raise ValueError("approval campaign execution closure path is invalid")
        handled, overridden = ga_file_resolution_override(raw_closure_path)
        if handled:
            if overridden is None:
                raise ValueError("execution closure has no verified portable file mapping")
            closure_path = overridden
        else:
            closure_path = Path(raw_closure_path).expanduser()
            if not closure_path.is_absolute():
                closure_path = freeze_path.resolve().parent / closure_path
        if closure_path.is_symlink() or not closure_path.is_file():
            raise ValueError("approval campaign execution closure is missing or symlinked")
        closure_path = closure_path.resolve()
        execution_closure_digest = sha256_path(closure_path)
        if (
            not DIGEST_RE.fullmatch(str(closure_reference.get("sha256", "")))
            or closure_reference.get("sha256") != execution_closure_digest
        ):
            raise ValueError("execution closure digest does not match the approval campaign freeze")
        try:
            from scripts.close_ga_execution_campaign import verify_persisted_closure
        except ModuleNotFoundError:  # direct script execution
            from close_ga_execution_campaign import verify_persisted_closure
        closure_validation = verify_persisted_closure(
            closure_path,
            authorization_path=expected_authorization_path,
            approval_policy_path=expected_policy_path,
            now=current,
        )
        if closure_validation.get("closure_sha256") != execution_closure_digest:
            raise ValueError("execution closure changed during approval campaign validation")
        if closure_validation.get("release_digest") != release_digest:
            raise ValueError("execution closure release digest differs from the approval freeze")

    expected_campaign_id = derive_campaign_id(
        authorization_sha256=authorization_digest,
        approval_policy_sha256=policy_digest,
        release_digest=release_digest,
        frozen_at=str(freeze["frozen_at"]),
        approvals_expire_at=str(freeze["approvals_expire_at"]),
        execution_closure_sha256=execution_closure_digest,
    )
    if freeze.get("campaign_id") != expected_campaign_id:
        raise ValueError("approval campaign id does not match its frozen inputs")
    evaluation = freeze.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("approval campaign freeze evaluation is invalid")
    if not (
        evaluation.get("status") == "AWAITING_EXTERNAL_APPROVALS"
        and evaluation.get("campaign_stage") == "APPROVAL_COLLECTION"
        and evaluation.get("foundation_ready") is True
        and evaluation.get("evidence_ready_for_approval") is True
        and evaluation.get("failed_foundation_checks") == []
        and evaluation.get("failed_evidence_checks") == []
        and evaluation.get("next_action") == "collect_organizational_approvals"
        and evaluation.get("release_digest") == release_digest
    ):
        raise ValueError("approval campaign freeze did not capture an approvable evaluation")
    return {
        "campaign_id": expected_campaign_id,
        "campaign_freeze_sha256": sha256_path(freeze_path),
        "frozen_at": frozen_at,
        "approvals_expire_at": expires_at,
        "release_digest": release_digest,
        "schema_version": schema_version,
        "execution_closure": closure_validation,
        "freeze": freeze,
    }
