"""Signed campaign binding for the real Kubernetes cluster and principal."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts.ga_execution_authorization import (
        MAXIMUM_CLOCK_SKEW,
        atomic_write,
        json_payload,
        load_approval_authority,
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
        json_payload,
        load_approval_authority,
        load_object,
        meaningful,
        parse_time,
        resolve_recorded_path,
        sha256,
        validate_campaign,
        verify_authorization,
    )
    from ga_path_resolution import ga_file_resolution_override


SCHEMA_VERSION = "duckdock-ga-target-cluster-identity-v1"
VERIFICATION_SCHEMA_VERSION = "duckdock-ga-target-cluster-identity-verification-v1"
SIGNATURE_NAMESPACE = "duckdock-ga-target-cluster-identity"
AUTHORIZATION_BOUNDARY = "attests_only_exact_target_cluster_identity_not_GA_or_mutation"
REPORT_ARTIFACT = "target_cluster_identity_report"
SIGNATURE_ARTIFACT = "target_cluster_identity_signature"
Observation = Callable[[str, str], dict[str, str]]


def _authorization_inputs(
    campaign: Mapping[str, Any], campaign_path: Path
) -> tuple[Path, dict[str, tuple[Path, Path]]]:
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


def _run_kubectl_json(context: str, arguments: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "kubectl",
                "--context",
                context,
                "--request-timeout=15s",
                *arguments,
                "-o",
                "json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot query Kubernetes target identity: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace")[:4096].strip()
        raise ValueError(f"Kubernetes target identity query failed: {detail}")
    if len(completed.stdout) > 256 * 1024:
        raise ValueError("Kubernetes target identity response exceeds 256 KiB")
    try:
        value = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Kubernetes target identity response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Kubernetes target identity response root must be an object")
    return value


def observe_target_cluster(context: str, namespace: str) -> dict[str, str]:
    whoami = _run_kubectl_json(context, ["auth", "whoami"])
    kube_system = _run_kubectl_json(context, ["get", "namespace", "kube-system"])
    status = whoami.get("status")
    user_info = status.get("userInfo") if isinstance(status, dict) else None
    metadata = kube_system.get("metadata")
    observation = {
        "context": context,
        "namespace": namespace,
        "cluster_uid": str(metadata.get("uid", "")) if isinstance(metadata, dict) else "",
        "principal": str(user_info.get("username", "")) if isinstance(user_info, dict) else "",
    }
    return _validated_observation(observation)


def _validated_observation(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "context",
        "namespace",
        "cluster_uid",
        "principal",
    }:
        raise ValueError("target cluster identity observation has invalid fields")
    if not all(meaningful(item) for item in value.values()):
        raise ValueError("target cluster identity observation contains a placeholder")
    if any(not isinstance(item, str) or not item.isprintable() for item in value.values()):
        raise ValueError("target cluster identity observation must contain printable strings")
    return {str(key): str(item) for key, item in value.items()}


def _expected_observation(campaign: Mapping[str, Any]) -> dict[str, str]:
    execution = campaign.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("execution campaign has no target execution controls")
    return _validated_observation(
        {
            "context": execution.get("kubernetes_context"),
            "namespace": execution.get("namespace"),
            "cluster_uid": execution.get("kubernetes_cluster_uid"),
            "principal": execution.get("kubernetes_principal"),
        }
    )


def prepare_target_cluster_identity(
    campaign_path: Path,
    *,
    operations_identity: str,
    observer: Observation = observe_target_cluster,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path, Path, Path]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign_path = campaign_path.resolve()
    campaign = validate_campaign(campaign_path)
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    if current < starts_at or current > expires_at:
        raise ValueError("target cluster identity must be collected inside the active campaign window")
    manifest_path, statements = _authorization_inputs(campaign, campaign_path)
    authorization = verify_authorization(
        campaign_path,
        manifest_path,
        statements,
        now=current,
    )
    operations = authorization.get("signers", {}).get("Operations")
    if not isinstance(operations, dict) or operations.get("identity") != operations_identity:
        raise ValueError("target cluster identity must use the exact Operations authorizer")
    expected = _expected_observation(campaign)
    observed = _validated_observation(
        observer(expected["context"], expected["namespace"])
    )
    if observed != expected:
        raise ValueError("live Kubernetes cluster UID or principal differs from the campaign")
    report_path = _planned_output_path(campaign_path, campaign, REPORT_ARTIFACT)
    signature_path = _planned_output_path(campaign_path, campaign, SIGNATURE_ARTIFACT)
    if report_path.exists() or signature_path.exists():
        raise ValueError("target cluster identity outputs are immutable and already exist")
    _, _, allowed_signers, _ = load_approval_authority(campaign_path, campaign)
    if authorization.get("allowed_signers") != {
        "path": str(allowed_signers),
        "sha256": sha256(allowed_signers),
    }:
        raise ValueError("execution authorization allowed-signers changed")
    report = {
        "schema_version": SCHEMA_VERSION,
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "authorization_id": authorization["authorization_id"],
        "campaign_id": campaign["campaign_id"],
        "campaign_sha256": sha256(campaign_path),
        "release": campaign["release"],
        "target_id": campaign["target"]["target_id"],
        "kubernetes": observed,
        "operator": {"role": "Operations", "identity": operations_identity},
        "observed_at": current.isoformat(),
    }
    return report, allowed_signers, report_path, signature_path


def sign_payload(payload: bytes, *, key: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="duckdock-ga-target-cluster-") as root:
        report_path = Path(root) / "target-cluster-identity.json"
        report_path.write_bytes(payload)
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
                    str(report_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except OSError as exc:
            raise ValueError(f"cannot execute ssh-keygen: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
            raise ValueError(f"cannot sign target cluster identity: {detail}")
        signature_path = Path(f"{report_path}.sig")
        if not signature_path.is_file():
            raise ValueError("ssh-keygen did not create a target cluster identity signature")
        return signature_path.read_bytes()


def _verify_signature(
    report_path: Path,
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
            input=report_path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify target cluster identity signature: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid target cluster identity signature: {detail}")


def verify_target_cluster_identity(
    campaign_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign_path = campaign_path.resolve()
    campaign = validate_campaign(campaign_path)
    manifest_path, statements = _authorization_inputs(campaign, campaign_path)
    authorization = verify_authorization(
        campaign_path,
        manifest_path,
        statements,
        now=current,
    )
    report_path = resolve_recorded_path(
        campaign.get("artifacts", {}).get(REPORT_ARTIFACT),
        base=campaign_path,
        label="target cluster identity report",
    )
    signature_path = resolve_recorded_path(
        campaign.get("artifacts", {}).get(SIGNATURE_ARTIFACT),
        base=campaign_path,
        label="target cluster identity signature",
    )
    report = load_object(report_path, "target cluster identity report")
    expected_keys = {
        "schema_version",
        "authorization_boundary",
        "authorization_id",
        "campaign_id",
        "campaign_sha256",
        "release",
        "target_id",
        "kubernetes",
        "operator",
        "observed_at",
    }
    observed_at = parse_time(report.get("observed_at"), "target cluster observed_at")
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    operations = authorization.get("signers", {}).get("Operations")
    operator = report.get("operator")
    if not (
        set(report) == expected_keys
        and report.get("schema_version") == SCHEMA_VERSION
        and report.get("authorization_boundary") == AUTHORIZATION_BOUNDARY
        and report.get("authorization_id") == authorization.get("authorization_id")
        and report.get("campaign_id") == campaign.get("campaign_id")
        and report.get("campaign_sha256") == sha256(campaign_path)
        and report.get("release") == campaign.get("release")
        and report.get("target_id") == campaign.get("target", {}).get("target_id")
        and _validated_observation(report.get("kubernetes")) == _expected_observation(campaign)
        and isinstance(operator, dict)
        and set(operator) == {"role", "identity"}
        and operator.get("role") == "Operations"
        and isinstance(operations, dict)
        and operator.get("identity") == operations.get("identity")
        and starts_at <= observed_at <= expires_at
        and observed_at <= current + MAXIMUM_CLOCK_SKEW
    ):
        raise ValueError("target cluster identity report is invalid or out of window")
    _, _, allowed_signers, _ = load_approval_authority(campaign_path, campaign)
    allowed_digest = sha256(allowed_signers)
    if authorization.get("allowed_signers") != {
        "path": str(allowed_signers),
        "sha256": allowed_digest,
    }:
        raise ValueError("execution authorization allowed-signers changed")
    tracked = {
        campaign_path: sha256(campaign_path),
        report_path: sha256(report_path),
        signature_path: sha256(signature_path),
        allowed_signers: allowed_digest,
    }
    _verify_signature(
        report_path,
        signature_path,
        identity=str(operator["identity"]),
        allowed_signers=allowed_signers,
    )
    final_authorization = verify_authorization(
        campaign_path,
        manifest_path,
        statements,
        now=current,
    )
    if final_authorization != authorization or any(
        sha256(path) != digest for path, digest in tracked.items()
    ):
        raise ValueError("target cluster identity inputs changed during verification")
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "status": "TARGET_CLUSTER_IDENTITY_VERIFIED",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "authorization_id": authorization["authorization_id"],
        "campaign_id": campaign["campaign_id"],
        "campaign_sha256": tracked[campaign_path],
        "target_id": report["target_id"],
        "kubernetes": report["kubernetes"],
        "operator": report["operator"],
        "observed_at": observed_at.isoformat(),
        "report": {"path": str(report_path), "sha256": tracked[report_path]},
        "signature": {"path": str(signature_path), "sha256": tracked[signature_path]},
        "verified_at": current.isoformat(),
    }


def verify_live_target_cluster_identity(
    campaign_path: Path,
    *,
    observer: Observation = observe_target_cluster,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    verified = verify_target_cluster_identity(campaign_path, now=current)
    expected = _validated_observation(verified["kubernetes"])
    live = _validated_observation(observer(expected["context"], expected["namespace"]))
    if live != expected:
        raise ValueError(
            "live Kubernetes cluster UID or principal changed after the signed identity receipt"
        )
    return {
        "schema_version": "duckdock-ga-target-cluster-runtime-verification-v1",
        "status": "LIVE_TARGET_CLUSTER_IDENTITY_VERIFIED",
        "campaign_id": verified["campaign_id"],
        "target_id": verified["target_id"],
        "kubernetes": live,
        "signed_report_sha256": verified["report"]["sha256"],
        "verified_at": current.isoformat(),
    }


def persist_target_cluster_identity(
    campaign_path: Path,
    *,
    operations_identity: str,
    key: Path,
    observer: Observation = observe_target_cluster,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report, _allowed_signers, report_path, signature_path = prepare_target_cluster_identity(
        campaign_path,
        operations_identity=operations_identity,
        observer=observer,
        now=current,
    )
    payload = json_payload(report)
    signature = sign_payload(payload, key=key)
    wrote_report = False
    wrote_signature = False
    try:
        atomic_write(report_path, payload)
        wrote_report = True
        atomic_write(signature_path, signature)
        wrote_signature = True
        return verify_target_cluster_identity(campaign_path, now=current)
    except Exception:
        if wrote_signature:
            signature_path.unlink(missing_ok=True)
        if wrote_report:
            report_path.unlink(missing_ok=True)
        raise
