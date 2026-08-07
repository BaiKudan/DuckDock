"""Signed live Kubernetes authorization profile for a GA execution campaign."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from scripts.ga_execution_authorization import (
        MAXIMUM_CLOCK_SKEW,
        atomic_write,
        json_payload,
        load_approval_authority,
        load_object,
        parse_time,
        resolve_recorded_path,
        sha256,
        validate_campaign,
        verify_authorization,
    )
    from scripts.ga_path_resolution import ga_file_resolution_override
    from scripts.ga_target_cluster_identity import (
        verify_live_target_cluster_identity,
        verify_target_cluster_identity,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_authorization import (
        MAXIMUM_CLOCK_SKEW,
        atomic_write,
        json_payload,
        load_approval_authority,
        load_object,
        parse_time,
        resolve_recorded_path,
        sha256,
        validate_campaign,
        verify_authorization,
    )
    from ga_path_resolution import ga_file_resolution_override
    from ga_target_cluster_identity import (
        verify_live_target_cluster_identity,
        verify_target_cluster_identity,
    )


SCHEMA_VERSION = "duckdock-ga-target-cluster-access-v1"
VERIFICATION_SCHEMA_VERSION = "duckdock-ga-target-cluster-access-verification-v1"
RUNTIME_SCHEMA_VERSION = "duckdock-ga-target-cluster-access-runtime-verification-v1"
SIGNATURE_NAMESPACE = "duckdock-ga-target-cluster-access"
AUTHORIZATION_BOUNDARY = "attests_only_fixed_live_RBAC_checks_not_GA_or_external_change_approval"
REPORT_ARTIFACT = "target_cluster_access_report"
SIGNATURE_ARTIFACT = "target_cluster_access_signature"
PermissionObserver = Callable[[str, Sequence[dict[str, Any]]], list[dict[str, Any]]]


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


def _check(
    expectation: str,
    verb: str,
    resource: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    return {
        "expectation": expectation,
        "verb": verb,
        "resource": resource,
        "namespace": namespace,
    }


def _check_sort_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("expectation")),
        str(value.get("namespace") or ""),
        str(value.get("verb")),
        str(value.get("resource")),
    )


def permission_profile(campaign: Mapping[str, Any]) -> list[dict[str, Any]]:
    execution = campaign.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("execution campaign has no Kubernetes execution controls")
    namespace = execution.get("namespace")
    scope = execution.get("kubernetes_scope")
    if not isinstance(namespace, str) or not isinstance(scope, dict):
        raise ValueError("execution campaign has no exact Kubernetes operational scope")

    required = [
        _check("ALLOW", "get", "/version"),
        _check("ALLOW", "get", "nodes"),
        _check("ALLOW", "list", "nodes"),
        _check("ALLOW", "patch", "nodes"),
        _check("ALLOW", "get", "networkpolicies.networking.k8s.io", namespace),
        _check("ALLOW", "list", "networkpolicies.networking.k8s.io", namespace),
        _check("ALLOW", "get", "deployments.apps", namespace),
        _check("ALLOW", "list", "deployments.apps", namespace),
        _check("ALLOW", "get", "pods", namespace),
        _check("ALLOW", "list", "pods", namespace),
        _check("ALLOW", "create", "pods/exec", namespace),
        _check("ALLOW", "create", "pods/eviction", namespace),
        _check("ALLOW", "get", "poddisruptionbudgets.policy", namespace),
        _check("ALLOW", "list", "poddisruptionbudgets.policy", namespace),
        _check("ALLOW", "get", f"secrets/{scope['secret_name']}", namespace),
        _check(
            "ALLOW",
            "get",
            f"daemonsets.apps/{scope['cni_daemonset_name']}",
            str(scope["cni_daemonset_namespace"]),
        ),
    ]
    for component in ("backend", "frontend", "worker"):
        required.append(
            _check("ALLOW", "patch", f"deployments.apps/{component}", namespace)
        )
    for prefix in ("trusted", "monitoring", "untrusted"):
        probe_namespace = str(scope[f"{prefix}_probe_namespace"])
        probe_pod = str(scope[f"{prefix}_probe_pod"])
        required.extend(
            [
                _check("ALLOW", "get", f"namespaces/{probe_namespace}"),
                _check("ALLOW", "get", f"pods/{probe_pod}", probe_namespace),
                _check("ALLOW", "create", "pods/exec", probe_namespace),
            ]
        )
    for bound_namespace in {
        namespace,
        str(scope["cni_daemonset_namespace"]),
    }:
        required.append(_check("ALLOW", "get", f"namespaces/{bound_namespace}"))

    forbidden = [
        _check("DENY", "*", "*", "*"),
        _check("DENY", "create", "namespaces"),
        _check("DENY", "delete", "namespaces"),
        _check("DENY", "delete", "nodes"),
        _check("DENY", "get", "secrets", "*"),
        _check("DENY", "list", "secrets", "*"),
        _check("DENY", "list", "secrets", namespace),
        _check("DENY", "watch", "secrets", namespace),
        _check("DENY", "get", "secrets/duckdock-ga-forbidden-sentinel", namespace),
        _check("DENY", "create", "secrets", namespace),
        _check("DENY", "update", "secrets", namespace),
        _check("DENY", "patch", "secrets", namespace),
        _check("DENY", "delete", "secrets", namespace),
        _check("DENY", "create", "pods", namespace),
        _check("DENY", "delete", "deployments.apps", namespace),
        _check("DENY", "create", "serviceaccounts/token", namespace),
        _check("DENY", "create", "subjectaccessreviews.authorization.k8s.io"),
        _check("DENY", "create", "tokenreviews.authentication.k8s.io"),
    ]
    for resource in (
        "clusterroles.rbac.authorization.k8s.io",
        "clusterrolebindings.rbac.authorization.k8s.io",
    ):
        for verb in ("create", "update", "patch", "delete"):
            forbidden.append(_check("DENY", verb, resource))
    forbidden.extend(
        [
            _check("DENY", "escalate", "clusterroles.rbac.authorization.k8s.io"),
            _check("DENY", "bind", "clusterroles.rbac.authorization.k8s.io"),
            _check("DENY", "impersonate", "users"),
            _check("DENY", "impersonate", "groups"),
            _check("DENY", "impersonate", "serviceaccounts"),
        ]
    )
    profile = sorted([*required, *forbidden], key=_check_sort_key)
    if len(profile) != len(
        {
            (
                item["expectation"],
                item["verb"],
                item["resource"],
                item["namespace"],
            )
            for item in profile
        }
    ):
        raise ValueError("Kubernetes authorization profile contains duplicate checks")
    return profile


def _kubectl_can_i(context: str, check: Mapping[str, Any]) -> bool:
    command = [
        "kubectl",
        "--context",
        context,
        "--request-timeout=15s",
        "auth",
        "can-i",
        str(check["verb"]),
        str(check["resource"]),
    ]
    namespace = check.get("namespace")
    if namespace == "*":
        command.append("--all-namespaces")
    elif isinstance(namespace, str):
        command.extend(["--namespace", namespace])
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot query Kubernetes authorization: {exc}") from exc
    stdout = completed.stdout.decode("utf-8", errors="replace").strip().lower()
    if stdout not in {"yes", "no"} or completed.returncode not in {0, 1}:
        detail = completed.stderr.decode("utf-8", errors="replace")[:4096].strip()
        raise ValueError(
            "Kubernetes authorization query failed: "
            f"verb={check['verb']} resource={check['resource']} detail={detail or stdout}"
        )
    return stdout == "yes"


def observe_permissions(
    context: str,
    checks: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {**check, "allowed": _kubectl_can_i(context, check)}
        for check in checks
    ]


def _validated_results(
    value: Any,
    *,
    expected: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(expected):
        raise ValueError("target cluster access results are incomplete")
    results: list[dict[str, Any]] = []
    for observed, check in zip(value, expected, strict=True):
        if not isinstance(observed, dict) or observed != {
            **check,
            "allowed": check["expectation"] == "ALLOW",
        }:
            raise ValueError("target Kubernetes permission differs from the least-privilege profile")
        results.append(dict(observed))
    return results


def _operational_scope(campaign: Mapping[str, Any]) -> dict[str, Any]:
    execution = campaign.get("execution")
    scope = execution.get("kubernetes_scope") if isinstance(execution, dict) else None
    if not isinstance(scope, dict):
        raise ValueError("execution campaign has no Kubernetes operational scope")
    return json.loads(json.dumps(scope, sort_keys=True))


def expected_phase_scope(campaign: Mapping[str, Any], phase_id: str) -> dict[str, str]:
    execution = campaign.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("execution campaign has no Kubernetes execution controls")
    scope = _operational_scope(campaign)
    common = {
        "context": str(execution["kubernetes_context"]),
        "namespace": str(execution["namespace"]),
    }
    if phase_id == "network":
        return {
            **common,
            "trusted_probe_namespace": str(scope["trusted_probe_namespace"]),
            "trusted_probe_pod": str(scope["trusted_probe_pod"]),
            "monitoring_probe_namespace": str(scope["monitoring_probe_namespace"]),
            "monitoring_probe_pod": str(scope["monitoring_probe_pod"]),
            "untrusted_probe_namespace": str(scope["untrusted_probe_namespace"]),
            "untrusted_probe_pod": str(scope["untrusted_probe_pod"]),
            "cni_daemonset_namespace": str(scope["cni_daemonset_namespace"]),
            "cni_daemonset_name": str(scope["cni_daemonset_name"]),
        }
    if phase_id == "secrets":
        return {**common, "secret_name": str(scope["secret_name"])}
    if phase_id == "high_availability":
        return {**common, "drain_zone": str(scope["ha_drain_zone"])}
    raise ValueError(f"phase has no Kubernetes operational scope binding: {phase_id}")


def _verified_authorization(
    campaign_path: Path,
    campaign: dict[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any], Path]:
    manifest_path, statements = _authorization_inputs(campaign, campaign_path)
    authorization = verify_authorization(
        campaign_path,
        manifest_path,
        statements,
        now=now,
    )
    return authorization, manifest_path


def prepare_target_cluster_access(
    campaign_path: Path,
    *,
    operations_identity: str,
    observer: PermissionObserver = observe_permissions,
    identity_observer: Callable[[str, str], dict[str, str]] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path, Path, Path]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign_path = campaign_path.resolve()
    campaign = validate_campaign(campaign_path)
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    if current < starts_at or current > expires_at:
        raise ValueError("target cluster access must be collected inside the active campaign window")
    authorization, _ = _verified_authorization(campaign_path, campaign, now=current)
    operations = authorization.get("signers", {}).get("Operations")
    if not isinstance(operations, dict) or operations.get("identity") != operations_identity:
        raise ValueError("target cluster access must use the exact Operations authorizer")
    if identity_observer is None:
        target_identity = verify_live_target_cluster_identity(campaign_path, now=current)
    else:
        target_identity = verify_live_target_cluster_identity(
            campaign_path,
            observer=identity_observer,
            now=current,
        )
    checks = permission_profile(campaign)
    context = str(campaign["execution"]["kubernetes_context"])
    results = _validated_results(observer(context, checks), expected=checks)
    artifacts = campaign.get("artifacts")
    release_provenance = resolve_recorded_path(
        artifacts.get("release_provenance") if isinstance(artifacts, dict) else None,
        base=campaign_path,
        label="release provenance",
    )
    report_path = _planned_output_path(campaign_path, campaign, REPORT_ARTIFACT)
    signature_path = _planned_output_path(campaign_path, campaign, SIGNATURE_ARTIFACT)
    if report_path.exists() or signature_path.exists():
        raise ValueError("target cluster access outputs are immutable and already exist")
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
        "release_provenance_sha256": sha256(release_provenance),
        "target_id": campaign["target"]["target_id"],
        "change_request_id": campaign["execution"]["change_request_id"],
        "kubernetes": target_identity["kubernetes"],
        "operational_scope": _operational_scope(campaign),
        "permission_results": results,
        "operator": {"role": "Operations", "identity": operations_identity},
        "observed_at": current.isoformat(),
    }
    return report, allowed_signers, report_path, signature_path


def sign_payload(payload: bytes, *, key: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="duckdock-ga-target-access-") as root:
        report_path = Path(root) / "target-cluster-access.json"
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
            raise ValueError(f"cannot sign target cluster access: {detail}")
        signature_path = Path(f"{report_path}.sig")
        if not signature_path.is_file():
            raise ValueError("ssh-keygen did not create a target cluster access signature")
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
        raise ValueError(f"cannot verify target cluster access signature: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid target cluster access signature: {detail}")


def verify_target_cluster_access(
    campaign_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign_path = campaign_path.resolve()
    campaign = validate_campaign(campaign_path)
    authorization, _ = _verified_authorization(campaign_path, campaign, now=current)
    artifacts = campaign.get("artifacts")
    report_path = resolve_recorded_path(
        artifacts.get(REPORT_ARTIFACT) if isinstance(artifacts, dict) else None,
        base=campaign_path,
        label="target cluster access report",
    )
    signature_path = resolve_recorded_path(
        artifacts.get(SIGNATURE_ARTIFACT) if isinstance(artifacts, dict) else None,
        base=campaign_path,
        label="target cluster access signature",
    )
    release_provenance = resolve_recorded_path(
        artifacts.get("release_provenance") if isinstance(artifacts, dict) else None,
        base=campaign_path,
        label="release provenance",
    )
    report = load_object(report_path, "target cluster access report")
    expected_keys = {
        "schema_version",
        "authorization_boundary",
        "authorization_id",
        "campaign_id",
        "campaign_sha256",
        "release",
        "release_provenance_sha256",
        "target_id",
        "change_request_id",
        "kubernetes",
        "operational_scope",
        "permission_results",
        "operator",
        "observed_at",
    }
    observed_at = parse_time(report.get("observed_at"), "target cluster access observed_at")
    starts_at = parse_time(campaign["window_starts_at"], "campaign window_starts_at")
    expires_at = parse_time(campaign["window_expires_at"], "campaign window_expires_at")
    operations = authorization.get("signers", {}).get("Operations")
    operator = report.get("operator")
    checks = permission_profile(campaign)
    results = _validated_results(report.get("permission_results"), expected=checks)
    target_identity = verify_target_cluster_identity(campaign_path, now=current)
    if not (
        set(report) == expected_keys
        and report.get("schema_version") == SCHEMA_VERSION
        and report.get("authorization_boundary") == AUTHORIZATION_BOUNDARY
        and report.get("authorization_id") == authorization.get("authorization_id")
        and report.get("campaign_id") == campaign.get("campaign_id")
        and report.get("campaign_sha256") == sha256(campaign_path)
        and report.get("release") == campaign.get("release")
        and report.get("release_provenance_sha256") == sha256(release_provenance)
        and report.get("target_id") == campaign.get("target", {}).get("target_id")
        and report.get("change_request_id")
        == campaign.get("execution", {}).get("change_request_id")
        and report.get("kubernetes") == target_identity.get("kubernetes")
        and report.get("operational_scope") == _operational_scope(campaign)
        and results == report.get("permission_results")
        and isinstance(operator, dict)
        and set(operator) == {"role", "identity"}
        and operator.get("role") == "Operations"
        and isinstance(operations, dict)
        and operator.get("identity") == operations.get("identity")
        and starts_at <= observed_at <= expires_at
        and observed_at <= current + MAXIMUM_CLOCK_SKEW
    ):
        raise ValueError("target cluster access report is invalid or out of window")
    _, _, allowed_signers, _ = load_approval_authority(campaign_path, campaign)
    allowed_digest = sha256(allowed_signers)
    if authorization.get("allowed_signers") != {
        "path": str(allowed_signers),
        "sha256": allowed_digest,
    }:
        raise ValueError("execution authorization allowed-signers changed")
    tracked = {
        campaign_path: sha256(campaign_path),
        release_provenance: sha256(release_provenance),
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
    final_authorization, _ = _verified_authorization(campaign_path, campaign, now=current)
    if final_authorization != authorization or any(
        sha256(path) != digest for path, digest in tracked.items()
    ):
        raise ValueError("target cluster access inputs changed during verification")
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "status": "TARGET_CLUSTER_ACCESS_VERIFIED",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "authorization_id": authorization["authorization_id"],
        "campaign_id": campaign["campaign_id"],
        "campaign_sha256": tracked[campaign_path],
        "target_id": report["target_id"],
        "change_request_id": report["change_request_id"],
        "kubernetes": report["kubernetes"],
        "operational_scope": report["operational_scope"],
        "permission_check_count": len(results),
        "operator": report["operator"],
        "observed_at": observed_at.isoformat(),
        "report": {"path": str(report_path), "sha256": tracked[report_path]},
        "signature": {"path": str(signature_path), "sha256": tracked[signature_path]},
        "verified_at": current.isoformat(),
    }


def verify_live_target_cluster_access(
    campaign_path: Path,
    *,
    phase_id: str,
    operational_scope: Mapping[str, Any],
    observer: PermissionObserver = observe_permissions,
    identity_observer: Callable[[str, str], dict[str, str]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    verified = verify_target_cluster_access(campaign_path, now=current)
    campaign = validate_campaign(campaign_path.resolve())
    expected_scope = expected_phase_scope(campaign, phase_id)
    if dict(operational_scope) != expected_scope:
        raise ValueError("runtime Kubernetes operational scope differs from the campaign")
    if identity_observer is None:
        live_identity = verify_live_target_cluster_identity(campaign_path, now=current)
    else:
        live_identity = verify_live_target_cluster_identity(
            campaign_path,
            observer=identity_observer,
            now=current,
        )
    if live_identity.get("kubernetes") != verified.get("kubernetes"):
        raise ValueError("runtime Kubernetes identity differs from the access receipt")
    checks = permission_profile(campaign)
    results = _validated_results(
        observer(str(verified["kubernetes"]["context"]), checks),
        expected=checks,
    )
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "status": "LIVE_TARGET_CLUSTER_ACCESS_VERIFIED",
        "campaign_id": verified["campaign_id"],
        "target_id": verified["target_id"],
        "change_request_id": verified["change_request_id"],
        "phase_id": phase_id,
        "operational_scope": expected_scope,
        "permission_check_count": len(results),
        "signed_report_sha256": verified["report"]["sha256"],
        "verified_at": current.isoformat(),
    }


def persist_target_cluster_access(
    campaign_path: Path,
    *,
    operations_identity: str,
    key: Path,
    observer: PermissionObserver = observe_permissions,
    identity_observer: Callable[[str, str], dict[str, str]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report, _allowed_signers, report_path, signature_path = prepare_target_cluster_access(
        campaign_path,
        operations_identity=operations_identity,
        observer=observer,
        identity_observer=identity_observer,
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
        return verify_target_cluster_access(campaign_path, now=current)
    except Exception:
        if wrote_signature:
            signature_path.unlink(missing_ok=True)
        if wrote_report:
            report_path.unlink(missing_ok=True)
        raise
