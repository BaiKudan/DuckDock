"""Signed, campaign-bound execution interlocks for risky GA phases."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.ga_execution_authorization import (
        MAXIMUM_CLOCK_SKEW,
        atomic_write,
        authorized_phases,
        checked_reference,
        json_payload,
        load_object,
        meaningful,
        parse_time,
        resolve_recorded_path,
        sha256,
        validate_campaign,
        verify_authorization,
    )
    from scripts.ga_path_resolution import ga_file_resolution_override
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_authorization import (
        MAXIMUM_CLOCK_SKEW,
        atomic_write,
        authorized_phases,
        checked_reference,
        json_payload,
        load_object,
        meaningful,
        parse_time,
        resolve_recorded_path,
        sha256,
        validate_campaign,
        verify_authorization,
    )
    from ga_path_resolution import ga_file_resolution_override


SCHEMA_VERSION = "duckdock-ga-execution-phase-start-v1"
SIGNATURE_NAMESPACE = "duckdock-ga-execution-phase-start"
AUTHORIZATION_BOUNDARY = (
    "authorizes_only_one_named_campaign_phase_start_not_GA_or_unlisted_mutation"
)
ACTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,127}$")
RISKY_PHASE_IDS = (
    "tls",
    "network",
    "secrets",
    "capacity",
    "alerting",
    "recovery",
    "state_services",
    "high_availability",
)


def artifact_keys(phase_id: str) -> tuple[str, str]:
    if phase_id not in RISKY_PHASE_IDS:
        raise ValueError(f"phase is not execution-interlocked: {phase_id}")
    return f"phase_start_{phase_id}_statement", f"phase_start_{phase_id}_signature"


def _action(action_id: str, description: str) -> dict[str, str]:
    if ACTION_ID_RE.fullmatch(action_id) is None:
        raise ValueError("action_id must be 8-128 safe, non-placeholder characters")
    if not meaningful(description) or not 8 <= len(description) <= 256:
        raise ValueError("action description must be 8-256 non-placeholder characters")
    if not description.isprintable() or any(character in description for character in "\r\n\t"):
        raise ValueError("action description must be one printable line")
    return {
        "action_id": action_id,
        "description": description,
        "sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
    }


def _planned_output_path(
    campaign_path: Path,
    campaign: Mapping[str, Any],
    artifact_name: str,
) -> Path:
    artifacts = campaign.get("artifacts")
    raw = artifacts.get(artifact_name) if isinstance(artifacts, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"campaign has no planned {artifact_name} artifact")
    handled, overridden = ga_file_resolution_override(raw)
    if handled:
        if overridden is None:
            raise ValueError(f"planned {artifact_name} has no verified portable mapping")
        path = overridden
    else:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = campaign_path.parent / path
    if path.is_symlink():
        raise ValueError(f"planned {artifact_name} must not be a symbolic link")
    return path.resolve()


def _verified_output_path(raw: Any, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path")
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _phase(campaign: Mapping[str, Any], phase_id: str) -> dict[str, Any]:
    matches = [phase for phase in authorized_phases(dict(campaign)) if phase["phase_id"] == phase_id]
    if len(matches) != 1 or phase_id not in RISKY_PHASE_IDS:
        raise ValueError(f"phase is not an exact authorized risky phase: {phase_id}")
    return matches[0]


def _authorization_inputs(campaign: Mapping[str, Any], campaign_path: Path) -> tuple[Path, dict[str, tuple[Path, Path]]]:
    artifacts = campaign.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("execution campaign has no planned artifacts")
    manifest = resolve_recorded_path(
        artifacts.get("execution_authorization_manifest"),
        base=campaign_path,
        label="execution authorization manifest",
    )
    statements: dict[str, tuple[Path, Path]] = {}
    for role in ("Security", "Operations"):
        slug = role.lower()
        statements[role] = (
            resolve_recorded_path(
                artifacts.get(f"execution_authorization_{slug}_statement"),
                base=campaign_path,
                label=f"{role} execution authorization statement",
            ),
            resolve_recorded_path(
                artifacts.get(f"execution_authorization_{slug}_signature"),
                base=campaign_path,
                label=f"{role} execution authorization signature",
            ),
        )
    return manifest, statements


def _dependency_artifacts(
    campaign: Mapping[str, Any],
    campaign_path: Path,
    phase: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    phases = campaign.get("phases")
    if not isinstance(phases, list):
        raise ValueError("execution campaign has no phase graph")
    by_id = {
        str(item.get("phase_id")): item
        for item in phases
        if isinstance(item, dict) and meaningful(item.get("phase_id"))
    }
    result: dict[str, dict[str, str]] = {}
    for dependency in phase["depends_on"]:
        dependency_phase = by_id.get(str(dependency))
        outputs = dependency_phase.get("outputs") if isinstance(dependency_phase, dict) else None
        if not isinstance(outputs, list) or not outputs:
            raise ValueError(f"phase dependency is invalid: {dependency}")
        for name in outputs:
            path = resolve_recorded_path(
                campaign.get("artifacts", {}).get(name),
                base=campaign_path,
                label=f"completed dependency artifact {name}",
            )
            result[str(name)] = {"path": str(path), "sha256": sha256(path)}
    return dict(sorted(result.items()))


def _validate_dependency_references(
    value: Any,
    *,
    expected: Mapping[str, Mapping[str, str]],
    statement_path: Path,
) -> tuple[bool, dict[Path, str]]:
    if not isinstance(value, dict) or set(value) != set(expected):
        return False, {}
    resolved: dict[Path, str] = {}
    for name, expected_reference in expected.items():
        try:
            path, digest = checked_reference(
                value.get(name),
                base=statement_path,
                label=f"phase-start dependency {name}",
            )
        except ValueError:
            return False, {}
        if path != Path(expected_reference["path"]).resolve() or digest != expected_reference["sha256"]:
            return False, {}
        resolved[path] = digest
    return True, resolved


def prepare_phase_start(
    campaign_path: Path,
    *,
    phase_id: str,
    action_id: str,
    action_description: str,
    operations_identity: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path, Path, Path]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign_path = campaign_path.resolve()
    campaign = validate_campaign(campaign_path)
    phase = _phase(campaign, phase_id)
    action = _action(action_id, action_description)
    if not meaningful(operations_identity):
        raise ValueError("Operations identity must be non-placeholder")
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    if current < starts_at or current > expires_at:
        raise ValueError("phase start must occur inside the active campaign window")
    manifest_path, statements = _authorization_inputs(campaign, campaign_path)
    authorization = verify_authorization(
        campaign_path,
        manifest_path,
        statements,
        now=current,
    )
    operations = authorization.get("signers", {}).get("Operations")
    if not isinstance(operations, dict) or operations.get("identity") != operations_identity:
        raise ValueError("phase start identity must be the exact Operations authorizer")
    dependencies = _dependency_artifacts(campaign, campaign_path, phase)
    statement_name, signature_name = artifact_keys(phase_id)
    statement_path = _planned_output_path(campaign_path, campaign, statement_name)
    signature_path = _planned_output_path(campaign_path, campaign, signature_name)
    if statement_path.exists() or signature_path.exists():
        raise ValueError("phase start outputs are immutable and already exist")
    allowed_signers = _verified_output_path(
        authorization.get("allowed_signers", {}).get("path"),
        label="execution authorization allowed-signers",
    )
    statement = {
        "schema_version": SCHEMA_VERSION,
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "authorization_id": authorization["authorization_id"],
        "campaign_id": campaign["campaign_id"],
        "campaign_sha256": sha256(campaign_path),
        "phase": phase,
        "required_acknowledgement": phase["required_acknowledgement"],
        "action": action,
        "dependency_artifacts": dependencies,
        "operator": {"role": "Operations", "identity": operations_identity},
        "started_at": current.isoformat(),
    }
    return statement, allowed_signers, statement_path, signature_path


def sign_payload(payload: bytes, *, key: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="duckdock-ga-phase-start-") as root:
        statement_path = Path(root) / "phase-start.json"
        statement_path.write_bytes(payload)
        try:
            completed = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-f",
                    str(key),
                    "-n",
                    SIGNATURE_NAMESPACE,
                    str(statement_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except OSError as exc:
            raise ValueError(f"cannot execute ssh-keygen: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
            raise ValueError(f"cannot sign GA phase start: {detail}")
        signature_path = Path(f"{statement_path}.sig")
        if not signature_path.is_file():
            raise ValueError("ssh-keygen did not create a phase-start signature")
        return signature_path.read_bytes()


def _verify_signature(
    statement_path: Path,
    signature_path: Path,
    *,
    identity: str,
    allowed_signers: Path,
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
                SIGNATURE_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            input=statement_path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify GA phase-start signature: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid GA phase-start signature: {detail}")


def verify_phase_start(
    campaign_path: Path,
    *,
    phase_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign_path = campaign_path.resolve()
    campaign = validate_campaign(campaign_path)
    phase = _phase(campaign, phase_id)
    manifest_path, statements = _authorization_inputs(campaign, campaign_path)
    authorization = verify_authorization(
        campaign_path,
        manifest_path,
        statements,
        now=current,
    )
    statement_name, signature_name = artifact_keys(phase_id)
    statement_path = resolve_recorded_path(
        campaign.get("artifacts", {}).get(statement_name),
        base=campaign_path,
        label=f"{phase_id} phase-start statement",
    )
    signature_path = resolve_recorded_path(
        campaign.get("artifacts", {}).get(signature_name),
        base=campaign_path,
        label=f"{phase_id} phase-start signature",
    )
    statement = load_object(statement_path, f"{phase_id} phase-start statement")
    expected_keys = {
        "schema_version",
        "authorization_boundary",
        "authorization_id",
        "campaign_id",
        "campaign_sha256",
        "phase",
        "required_acknowledgement",
        "action",
        "dependency_artifacts",
        "operator",
        "started_at",
    }
    action = statement.get("action")
    operator = statement.get("operator")
    started_at = parse_time(statement.get("started_at"), "phase started_at")
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    operations = authorization.get("signers", {}).get("Operations")
    dependencies = statement.get("dependency_artifacts")
    expected_dependencies = _dependency_artifacts(campaign, campaign_path, phase)
    dependencies_valid, resolved_dependencies = _validate_dependency_references(
        dependencies,
        expected=expected_dependencies,
        statement_path=statement_path,
    )
    if not (
        set(statement) == expected_keys
        and statement.get("schema_version") == SCHEMA_VERSION
        and statement.get("authorization_boundary") == AUTHORIZATION_BOUNDARY
        and statement.get("authorization_id") == authorization.get("authorization_id")
        and statement.get("campaign_id") == campaign.get("campaign_id")
        and statement.get("campaign_sha256") == sha256(campaign_path)
        and statement.get("phase") == phase
        and statement.get("required_acknowledgement") == phase.get("required_acknowledgement")
        and isinstance(action, dict)
        and set(action) == {"action_id", "description", "sha256"}
        and ACTION_ID_RE.fullmatch(str(action.get("action_id", ""))) is not None
        and _action(str(action.get("action_id")), str(action.get("description"))) == action
        and dependencies_valid
        and isinstance(operator, dict)
        and set(operator) == {"role", "identity"}
        and operator.get("role") == "Operations"
        and isinstance(operations, dict)
        and operator.get("identity") == operations.get("identity")
        and starts_at <= started_at <= expires_at
        and started_at <= current + MAXIMUM_CLOCK_SKEW
    ):
        raise ValueError(f"{phase_id} phase-start statement is invalid or out of window")
    allowed_signers = _verified_output_path(
        authorization.get("allowed_signers", {}).get("path"),
        label="execution authorization allowed-signers",
    )
    tracked = {
        campaign_path: sha256(campaign_path),
        manifest_path: sha256(manifest_path),
        statement_path: sha256(statement_path),
        signature_path: sha256(signature_path),
        allowed_signers: sha256(allowed_signers),
        **resolved_dependencies,
    }
    _verify_signature(
        statement_path,
        signature_path,
        identity=str(operator["identity"]),
        allowed_signers=allowed_signers,
    )
    if any(sha256(path) != digest for path, digest in tracked.items()):
        raise ValueError("phase-start inputs changed during verification")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PHASE_START_AUTHORIZED",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "authorization_id": authorization["authorization_id"],
        "campaign_id": campaign["campaign_id"],
        "phase_id": phase_id,
        "action": action,
        "operator": operator,
        "started_at": started_at.isoformat(),
        "statement": {"path": str(statement_path), "sha256": tracked[statement_path]},
        "signature": {"path": str(signature_path), "sha256": tracked[signature_path]},
        "dependency_artifact_count": len(expected_dependencies),
        "verified_at": current.isoformat(),
    }


def persist_phase_start(
    campaign_path: Path,
    *,
    phase_id: str,
    action_id: str,
    action_description: str,
    operations_identity: str,
    key: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    statement, _allowed_signers, statement_path, signature_path = prepare_phase_start(
        campaign_path,
        phase_id=phase_id,
        action_id=action_id,
        action_description=action_description,
        operations_identity=operations_identity,
        now=current,
    )
    payload = json_payload(statement)
    signature = sign_payload(payload, key=key)
    wrote_statement = False
    wrote_signature = False
    try:
        atomic_write(statement_path, payload)
        wrote_statement = True
        atomic_write(signature_path, signature)
        wrote_signature = True
        return verify_phase_start(campaign_path, phase_id=phase_id, now=current)
    except Exception:
        if wrote_signature:
            signature_path.unlink(missing_ok=True)
        if wrote_statement:
            statement_path.unlink(missing_ok=True)
        raise
