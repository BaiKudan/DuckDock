#!/usr/bin/env python3
"""Close a GA execution campaign and assemble its approval-empty authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.archive_ga_authorized_bundle import (
        AllowedRoot,
        CapturedFile,
        CapturedReference,
        _capture_reference_closure,
    )
    from scripts.assemble_ga_preapproval_authorization import assemble
    from scripts.ga_path_resolution import ga_file_resolution_override
    from scripts.prepare_ga_execution_campaign import (
        PLAN_SCHEMA_VERSION,
        _parse_time,
        prepare,
    )
    from scripts.verify_ga_production_authorization import evaluate
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from archive_ga_authorized_bundle import (
        AllowedRoot,
        CapturedFile,
        CapturedReference,
        _capture_reference_closure,
    )
    from assemble_ga_preapproval_authorization import assemble
    from ga_path_resolution import ga_file_resolution_override
    from prepare_ga_execution_campaign import PLAN_SCHEMA_VERSION, _parse_time, prepare
    from verify_ga_production_authorization import evaluate


CLOSURE_SCHEMA_VERSION = "duckdock-ga-execution-campaign-closure-v1"
CLOSURE_KEYS = {
    "schema_version",
    "status",
    "authorization_boundary",
    "closed_at",
    "campaign_id",
    "campaign",
    "assembly_request",
    "approval_policy",
    "external_artifact_count",
    "external_artifacts",
    "captured_input_count",
    "reference_count",
    "references",
    "outputs",
    "evaluation",
    "next_action",
}
CLOSURE_OUTPUT_KEYS = {
    "preapproval_authorization",
    "preapproval_assembly_receipt",
    "execution_closure",
}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINAL_EVIDENCE_KEYS = (
    "release_provenance",
    "application_readiness",
    "tls",
    "secrets",
    "network",
    "alerting",
    "recovery",
    "capacity",
    "high_availability",
    "security_assessment",
)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _checked_reference(value: Any, *, label: str) -> tuple[Path, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError(f"{label} must contain exactly path and sha256")
    raw_path = value.get("path")
    digest = value.get("sha256")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label}.path must be a non-empty path")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label}.sha256 must be SHA-256")
    handled, overridden = ga_file_resolution_override(raw_path)
    if handled:
        if overridden is None:
            raise ValueError(f"{label} has no verified portable file mapping")
        candidate = overridden
    else:
        candidate = Path(raw_path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} is not a regular non-symlink file: {candidate}")
    path = candidate.resolve()
    if not path.is_file():
        raise ValueError(f"{label} is not a regular non-symlink file: {path}")
    if _sha256(path) != digest:
        raise ValueError(f"{label} digest mismatch")
    return path, digest


def _resolved_recorded_path(raw_path: Any, *, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label} must be a non-empty path")
    handled, overridden = ga_file_resolution_override(raw_path)
    if handled:
        if overridden is None:
            raise ValueError(f"{label} has no verified portable file mapping")
        candidate = overridden
    else:
        candidate = Path(raw_path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} is a forbidden symbolic link: {candidate}")
    return candidate.resolve()


def _exact_roots(paths: set[Path]) -> list[AllowedRoot]:
    parents = sorted({path.parent.resolve() for path in paths}, key=str)
    return [
        AllowedRoot(label=f"root{index:02d}", path=parent)
        for index, parent in enumerate(parents)
    ]


def _capture_exact_closure(
    seeds: set[Path],
    *,
    allowed_paths: set[Path],
    authorization_path: Path,
) -> tuple[list[CapturedFile], list[CapturedReference]]:
    normalized_seeds = {path.resolve() for path in seeds}
    normalized_allowed = {path.resolve() for path in allowed_paths}
    if not normalized_seeds or not normalized_seeds.issubset(normalized_allowed):
        raise ValueError("closure seeds must be a non-empty subset of exact allowed paths")
    captured, references = _capture_reference_closure(
        sorted(normalized_seeds, key=str),
        authorization_path=authorization_path.resolve(),
        roots=_exact_roots(normalized_allowed),
        max_file_bytes=MAX_FILE_BYTES,
        max_total_bytes=MAX_TOTAL_BYTES,
    )
    captured_paths = {item.path for item in captured}
    if captured_paths != normalized_allowed:
        missing = sorted(str(path) for path in normalized_allowed - captured_paths)
        unexpected = sorted(str(path) for path in captured_paths - normalized_allowed)
        raise ValueError(
            "campaign evidence closure is not exact: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if any(reference.target not in normalized_allowed for reference in references):
        raise ValueError("campaign evidence contains a reference to an unplanned path")
    return captured, references


def _topology_inputs(
    receipt: dict[str, Any],
) -> tuple[dict[Path, str], Path, dict[Path, str]]:
    tracked: dict[Path, str] = {}
    recorded_paths: dict[Path, str] = {}
    manifest_reference = receipt.get("manifest")
    manifest_path, manifest_digest = _checked_reference(
        manifest_reference, label="trust topology manifest"
    )
    tracked[manifest_path] = manifest_digest
    recorded_paths[manifest_path] = str(manifest_reference["path"])
    policies = receipt.get("policies")
    if not isinstance(policies, dict) or "approval" not in policies:
        raise ValueError("trust topology receipt has no approval policy")
    approval_policy: Path | None = None
    for name, policy in policies.items():
        if not isinstance(policy, dict):
            raise ValueError(f"trust topology {name} policy record is invalid")
        policy_path, policy_digest = _checked_reference(
            policy.get("policy"), label=f"trust topology {name} policy"
        )
        trust_path, trust_digest = _checked_reference(
            policy.get("allowed_signers"),
            label=f"trust topology {name} allowed-signers",
        )
        tracked[policy_path] = policy_digest
        tracked[trust_path] = trust_digest
        recorded_paths[policy_path] = str(policy["policy"]["path"])
        recorded_paths[trust_path] = str(policy["allowed_signers"]["path"])
        if name == "approval":
            approval_policy = policy_path
    if approval_policy is None:
        raise ValueError("trust topology receipt has no approval policy path")
    return tracked, approval_policy, recorded_paths


def _label_paths(
    artifacts: dict[str, Path],
    fixed: dict[str, Path],
) -> dict[Path, str]:
    labels = {path: f"artifact:{name}" for name, path in artifacts.items()}
    for name, path in fixed.items():
        labels.setdefault(path, name)
    return labels


def _validate_observation_window(
    artifacts: dict[str, Path],
    *,
    starts_at: datetime,
    closed_at: datetime,
) -> None:
    for name in FINAL_EVIDENCE_KEYS:
        path = artifacts.get(name)
        if path is None:
            raise ValueError(f"campaign has no final {name} evidence")
        report = _load_object(path, f"campaign {name} evidence")
        observed_at = _parse_time(
            report.get("observed_at"), f"{name} evidence observed_at"
        )
        if observed_at < starts_at or observed_at > closed_at:
            raise ValueError(
                f"campaign {name} evidence was not observed inside the execution window"
            )


def _validate_security_assessment_campaign_window(
    artifacts: dict[str, Path],
    *,
    campaign_created_at: datetime,
    campaign_starts_at: datetime,
    campaign_expires_at: datetime,
) -> None:
    path = artifacts.get("security_assessment_engagement")
    if path is None:
        raise ValueError("campaign has no security assessment engagement")
    engagement = _load_object(path, "campaign security assessment engagement")
    authorized_at = _parse_time(
        engagement.get("authorized_at"), "security engagement authorized_at"
    )
    window = engagement.get("authorization_window")
    if not isinstance(window, dict):
        raise ValueError("security engagement has no authorization window")
    engagement_starts_at = _parse_time(
        window.get("starts_at"), "security engagement starts_at"
    )
    engagement_expires_at = _parse_time(
        window.get("expires_at"), "security engagement expires_at"
    )
    if authorized_at < campaign_created_at:
        raise ValueError("security engagement was authorized before campaign creation")
    if not (
        campaign_starts_at <= engagement_starts_at
        and engagement_starts_at < engagement_expires_at
        and engagement_expires_at <= campaign_expires_at
    ):
        raise ValueError("security assessment window is outside the execution campaign")


def _reference_ledger(
    references: Sequence[CapturedReference],
    labels: dict[Path, str],
) -> list[dict[str, Any]]:
    return [
        {
            "source": labels.get(reference.source, str(reference.source)),
            "target": labels.get(reference.target, str(reference.target)),
            "reference_type": reference.reference_type,
            "raw_path": reference.raw_path,
            "declared_sha256": reference.declared_sha256,
        }
        for reference in sorted(
            references,
            key=lambda item: (
                str(item.source),
                item.reference_type,
                item.raw_path,
                str(item.target),
            ),
        )
    ]


def _evaluation_verdict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("execution closure evaluation must be an object")
    checks = value.get("checks")
    if not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks):
        raise ValueError("execution closure evaluation checks must be an array of objects")
    return {
        **{key: item for key, item in value.items() if key != "checks"},
        "checks": [
            {
                "key": check.get("key"),
                "owner": check.get("owner"),
                "passed": check.get("passed"),
                "status": check.get("status"),
            }
            for check in checks
        ],
    }


def _reference_record_sort_key(item: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(item.get("source")),
        str(item.get("target")),
        str(item.get("reference_type")),
        str(item.get("raw_path")),
        str(item.get("declared_sha256")),
    )


def close(
    campaign_path: Path,
    assembly_request_path: Path,
    *,
    now: datetime | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[Path, str],
]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    campaign_path = campaign_path.resolve()
    assembly_request_path = assembly_request_path.resolve()
    campaign = _load_object(campaign_path, "execution campaign")
    if campaign.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("execution campaign schema is unsupported")
    if not (
        campaign.get("status") == "PLANNED_EXTERNAL_EXECUTION"
        and campaign.get("authorization_boundary")
        == "does_not_authorize_GA_or_target_mutation"
    ):
        raise ValueError("execution campaign is not a non-authorizing planned campaign")
    starts_at = _parse_time(campaign.get("window_starts_at"), "window_starts_at")
    expires_at = _parse_time(campaign.get("window_expires_at"), "window_expires_at")
    if current < starts_at or current > expires_at:
        raise ValueError("execution campaign can close only inside its bound execution window")

    campaign_request_path, _ = _checked_reference(
        campaign.get("request"), label="execution campaign request"
    )
    topology = campaign.get("trust_topology")
    if not isinstance(topology, dict):
        raise ValueError("execution campaign has no trust topology")
    topology_receipt_path, _ = _checked_reference(
        topology.get("receipt"), label="trust topology receipt"
    )
    assembly_reference = campaign.get("preapproval_assembly_request")
    checked_assembly_path, _ = _checked_reference(
        assembly_reference, label="preapproval assembly request"
    )
    if checked_assembly_path != assembly_request_path:
        raise ValueError("preapproval assembly request path differs from the campaign")

    created_at = _parse_time(campaign.get("created_at"), "campaign created_at")
    regenerated_campaign, regenerated_request = prepare(
        campaign_request_path,
        topology_receipt_path,
        assembly_request_path,
        now=created_at,
        require_fresh_evidence_root=False,
    )
    if regenerated_campaign != campaign:
        raise ValueError("execution campaign did not independently re-verify")
    if _load_object(assembly_request_path, "preapproval assembly request") != regenerated_request:
        raise ValueError("preapproval assembly request did not independently re-verify")

    artifacts_value = campaign.get("artifacts")
    if not isinstance(artifacts_value, dict):
        raise ValueError("execution campaign artifacts must be an object")
    artifacts = {
        str(name): _resolved_recorded_path(path, label=f"campaign artifact {name}")
        for name, path in artifacts_value.items()
    }
    if CLOSURE_OUTPUT_KEYS - set(artifacts):
        raise ValueError("execution campaign is missing closure outputs")
    output_paths = {name: artifacts[name] for name in CLOSURE_OUTPUT_KEYS}
    if len(set(output_paths.values())) != len(CLOSURE_OUTPUT_KEYS):
        raise ValueError("execution campaign closure outputs must be distinct")
    if any(path.exists() for path in output_paths.values()):
        raise ValueError("execution campaign closure outputs are immutable and must not exist")
    planned_inputs = {
        name: path for name, path in artifacts.items() if name not in CLOSURE_OUTPUT_KEYS
    }
    _validate_observation_window(
        planned_inputs,
        starts_at=starts_at,
        closed_at=current,
    )
    _validate_security_assessment_campaign_window(
        planned_inputs,
        campaign_created_at=created_at,
        campaign_starts_at=starts_at,
        campaign_expires_at=expires_at,
    )

    topology_receipt = _load_object(topology_receipt_path, "trust topology receipt")
    topology_inputs, approval_policy_path, topology_recorded_paths = _topology_inputs(
        topology_receipt
    )
    execution = campaign.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("execution campaign has no normalized execution controls")
    backup_path, backup_digest = _checked_reference(
        execution.get("backup_allowed_signers"),
        label="campaign backup allowed-signers",
    )
    fixed_paths = {
        "campaign": campaign_path,
        "campaign_request": campaign_request_path,
        "assembly_request": assembly_request_path,
        "topology_receipt": topology_receipt_path,
        "backup_allowed_signers": backup_path,
        **{
            f"topology_input:{index}": path
            for index, path in enumerate(
                sorted(topology_inputs, key=lambda item: topology_recorded_paths[item])
            )
        },
    }
    allowed_paths = set(planned_inputs.values()) | set(fixed_paths.values())
    captured, references = _capture_exact_closure(
        allowed_paths,
        allowed_paths=allowed_paths,
        authorization_path=output_paths["preapproval_authorization"],
    )
    captured_digests = {item.path: item.sha256 for item in captured}
    if captured_digests.get(backup_path) != backup_digest:
        raise ValueError("backup allowed-signers changed during closure validation")
    labels = _label_paths(planned_inputs, fixed_paths)

    assembly_args = argparse.Namespace(
        request=assembly_request_path,
        approval_policy=approval_policy_path,
        output=output_paths["preapproval_authorization"],
        receipt_output=output_paths["preapproval_assembly_receipt"],
    )
    authorization, assembly_receipt, assembler_tracked = assemble(
        assembly_args,
        now=current,
    )
    if any(captured_digests.get(path) != digest for path, digest in assembler_tracked.items()):
        raise ValueError("preapproval assembler consumed an input outside the exact campaign closure")
    if any(_sha256(path) != digest for path, digest in captured_digests.items()):
        raise ValueError("campaign input changed during closure evaluation")

    artifact_ledger = {
        name: {
            "path": str(path),
            "sha256": captured_digests[path],
            "size_bytes": path.stat().st_size,
        }
        for name, path in sorted(planned_inputs.items())
    }
    reference_ledger = _reference_ledger(references, labels)
    closure = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "status": "PREAPPROVAL_ASSEMBLED",
        "authorization_boundary": "does_not_authorize_GA_or_target_mutation",
        "closed_at": current.isoformat(),
        "campaign_id": campaign["campaign_id"],
        "campaign": {"path": str(campaign_path), "sha256": _sha256(campaign_path)},
        "assembly_request": {
            "path": str(assembly_request_path),
            "sha256": _sha256(assembly_request_path),
        },
        "approval_policy": {
            "path": str(approval_policy_path),
            "sha256": topology_inputs[approval_policy_path],
        },
        "external_artifact_count": len(planned_inputs),
        "external_artifacts": artifact_ledger,
        "captured_input_count": len(captured),
        "reference_count": len(reference_ledger),
        "references": reference_ledger,
        "outputs": {},
        "evaluation": assembly_receipt["evaluation"],
        "next_action": "freeze_preapproval_campaign_then_collect_organizational_approvals",
    }
    return authorization, assembly_receipt, closure, captured_digests


def verify_persisted_closure(
    closure_path: Path,
    *,
    authorization_path: Path,
    approval_policy_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    closure_path = closure_path.resolve()
    authorization_path = authorization_path.resolve()
    approval_policy_path = approval_policy_path.resolve()
    closure = _load_object(closure_path, "execution campaign closure")
    if set(closure) != CLOSURE_KEYS:
        raise ValueError("execution campaign closure has invalid fields")
    if not (
        closure.get("schema_version") == CLOSURE_SCHEMA_VERSION
        and closure.get("status") == "PREAPPROVAL_ASSEMBLED"
        and closure.get("authorization_boundary")
        == "does_not_authorize_GA_or_target_mutation"
        and closure.get("next_action")
        == "freeze_preapproval_campaign_then_collect_organizational_approvals"
    ):
        raise ValueError("execution campaign closure status or boundary is invalid")
    closed_at = _parse_time(closure.get("closed_at"), "closure closed_at")
    if closed_at > current + timedelta(minutes=5):
        raise ValueError("execution campaign closure time is in the future")

    campaign_path, campaign_digest = _checked_reference(
        closure.get("campaign"), label="closed execution campaign"
    )
    assembly_request_path, assembly_request_digest = _checked_reference(
        closure.get("assembly_request"), label="closed preapproval assembly request"
    )
    closed_policy_path, closed_policy_digest = _checked_reference(
        closure.get("approval_policy"), label="closed approval policy"
    )
    if closed_policy_path != approval_policy_path:
        raise ValueError("execution closure approval policy path mismatch")

    campaign = _load_object(campaign_path, "closed execution campaign")
    if campaign.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("closed execution campaign schema is unsupported")
    if closure.get("campaign_id") != campaign.get("campaign_id"):
        raise ValueError("execution closure campaign ID mismatch")
    campaign_request_path, _ = _checked_reference(
        campaign.get("request"), label="closed campaign request"
    )
    campaign_reference = closure.get("campaign")
    portable_resolution, _ = ga_file_resolution_override(
        campaign_reference.get("path") if isinstance(campaign_reference, dict) else None
    )
    topology = campaign.get("trust_topology")
    if not isinstance(topology, dict):
        raise ValueError("closed execution campaign has no trust topology")
    topology_receipt_path, _ = _checked_reference(
        topology.get("receipt"), label="closed trust topology receipt"
    )
    checked_assembly_path, checked_assembly_digest = _checked_reference(
        campaign.get("preapproval_assembly_request"),
        label="campaign preapproval assembly request",
    )
    if (
        checked_assembly_path != assembly_request_path
        or checked_assembly_digest != assembly_request_digest
    ):
        raise ValueError("execution closure assembly request differs from the campaign")
    if portable_resolution:
        if _sha256(campaign_path) != campaign_digest:
            raise ValueError("closed execution campaign digest did not re-verify")
    else:
        created_at = _parse_time(campaign.get("created_at"), "campaign created_at")
        regenerated_campaign, regenerated_request = prepare(
            campaign_request_path,
            topology_receipt_path,
            assembly_request_path,
            now=created_at,
            require_fresh_evidence_root=False,
        )
        if regenerated_campaign != campaign or _sha256(campaign_path) != campaign_digest:
            raise ValueError("closed execution campaign did not independently re-verify")
        if _load_object(assembly_request_path, "closed assembly request") != regenerated_request:
            raise ValueError("closed preapproval assembly request did not independently re-verify")

    artifacts_value = campaign.get("artifacts")
    if not isinstance(artifacts_value, dict):
        raise ValueError("closed execution campaign artifacts must be an object")
    artifacts = {
        str(name): _resolved_recorded_path(path, label=f"campaign artifact {name}")
        for name, path in artifacts_value.items()
    }
    if CLOSURE_OUTPUT_KEYS - set(artifacts):
        raise ValueError("closed execution campaign is missing closure outputs")
    planned_inputs = {
        name: path for name, path in artifacts.items() if name not in CLOSURE_OUTPUT_KEYS
    }
    external_artifacts = closure.get("external_artifacts")
    if (
        not isinstance(external_artifacts, dict)
        or set(external_artifacts) != set(planned_inputs)
        or closure.get("external_artifact_count") != len(planned_inputs)
    ):
        raise ValueError("execution closure external artifact ledger is incomplete")
    for name, path in planned_inputs.items():
        record = external_artifacts.get(name)
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size_bytes"}:
            raise ValueError(f"execution closure artifact record is invalid: {name}")
        if _resolved_recorded_path(
            record.get("path"), label=f"execution closure artifact {name}"
        ) != path:
            raise ValueError(f"execution closure artifact path mismatch: {name}")
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"execution closure artifact is missing or symlinked: {name}")
        if (
            record.get("sha256") != _sha256(path)
            or record.get("size_bytes") != path.stat().st_size
        ):
            raise ValueError(f"execution closure artifact content mismatch: {name}")

    starts_at = _parse_time(campaign.get("window_starts_at"), "window_starts_at")
    expires_at = _parse_time(campaign.get("window_expires_at"), "window_expires_at")
    if closed_at < starts_at or closed_at > expires_at:
        raise ValueError("execution closure was emitted outside the campaign window")
    _validate_observation_window(
        planned_inputs,
        starts_at=starts_at,
        closed_at=closed_at,
    )
    _validate_security_assessment_campaign_window(
        planned_inputs,
        campaign_created_at=_parse_time(
            campaign.get("created_at"), "campaign created_at"
        ),
        campaign_starts_at=starts_at,
        campaign_expires_at=expires_at,
    )
    topology_receipt = _load_object(topology_receipt_path, "closed topology receipt")
    topology_inputs, topology_policy_path, topology_recorded_paths = _topology_inputs(
        topology_receipt
    )
    if (
        topology_policy_path != approval_policy_path
        or topology_inputs.get(approval_policy_path) != closed_policy_digest
    ):
        raise ValueError("execution closure policy does not match the trust topology")
    execution = campaign.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("closed execution campaign has no execution controls")
    backup_path, _ = _checked_reference(
        execution.get("backup_allowed_signers"),
        label="closed campaign backup allowed-signers",
    )
    fixed_paths = {
        "campaign": campaign_path,
        "campaign_request": campaign_request_path,
        "assembly_request": assembly_request_path,
        "topology_receipt": topology_receipt_path,
        "backup_allowed_signers": backup_path,
        **{
            f"topology_input:{index}": path
            for index, path in enumerate(
                sorted(topology_inputs, key=lambda item: topology_recorded_paths[item])
            )
        },
    }
    allowed_paths = set(planned_inputs.values()) | set(fixed_paths.values())
    captured, references = _capture_exact_closure(
        allowed_paths,
        allowed_paths=allowed_paths,
        authorization_path=authorization_path,
    )
    labels = _label_paths(planned_inputs, fixed_paths)
    expected_references = _reference_ledger(references, labels)
    recorded_references = closure.get("references")
    if closure.get("captured_input_count") != len(captured):
        raise ValueError("execution closure captured-input count did not independently re-verify")
    if closure.get("reference_count") != len(expected_references):
        raise ValueError("execution closure reference count did not independently re-verify")
    if not isinstance(recorded_references, list) or sorted(
        recorded_references, key=_reference_record_sort_key
    ) != sorted(expected_references, key=_reference_record_sort_key):
        raise ValueError("execution closure reference ledger did not independently re-verify")

    outputs = closure.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {
        "preapproval_authorization",
        "preapproval_assembly_receipt",
    }:
        raise ValueError("execution closure output bindings are invalid")
    closed_authorization_path, _ = _checked_reference(
        outputs.get("preapproval_authorization"),
        label="closed preapproval authorization",
    )
    assembly_receipt_path, _ = _checked_reference(
        outputs.get("preapproval_assembly_receipt"),
        label="closed preapproval assembly receipt",
    )
    if (
        closed_authorization_path != authorization_path
        or authorization_path != artifacts["preapproval_authorization"]
        or assembly_receipt_path != artifacts["preapproval_assembly_receipt"]
        or closure_path != artifacts["execution_closure"]
    ):
        raise ValueError("execution closure outputs do not match the planned paths")
    assembly_receipt = _load_object(
        assembly_receipt_path, "closed preapproval assembly receipt"
    )
    receipt_authorization_path, receipt_authorization_digest = _checked_reference(
        assembly_receipt.get("authorization"),
        label="assembly receipt authorization",
    )
    if (
        receipt_authorization_path != authorization_path
        or receipt_authorization_digest != _sha256(authorization_path)
    ):
        raise ValueError("assembly receipt does not bind the closed authorization")
    authorization = _load_object(authorization_path, "closed preapproval authorization")
    result = evaluate(
        authorization,
        authorization_path=authorization_path,
        approval_policy_path=approval_policy_path,
        now=closed_at,
    )
    recorded_evaluation = closure.get("evaluation")
    if not (
        result.get("status") == "AWAITING_EXTERNAL_APPROVALS"
        and result.get("campaign_stage") == "APPROVAL_COLLECTION"
        and result.get("foundation_ready") is True
        and result.get("evidence_ready_for_approval") is True
        and result.get("failed_foundation_checks") == []
        and result.get("failed_evidence_checks") == []
        and recorded_evaluation == assembly_receipt.get("evaluation")
        and _evaluation_verdict(result) == _evaluation_verdict(recorded_evaluation)
    ):
        raise ValueError("closed preapproval authorization did not independently re-verify")
    if any(_sha256(item.path) != item.sha256 for item in captured):
        raise ValueError("execution closure input changed during independent verification")
    return {
        "closure_sha256": _sha256(closure_path),
        "campaign_id": campaign["campaign_id"],
        "closed_at": closed_at,
        "authorization_sha256": _sha256(authorization_path),
        "approval_policy_sha256": _sha256(approval_policy_path),
        "release_digest": result["release_digest"],
        "closure": closure,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError(f"output already exists: {path}") from exc
        temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--assembly-request", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.campaign, "execution campaign"),
            (args.assembly_request, "preapproval assembly request"),
        ):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} is not a regular non-symlink file: {path}")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    created_outputs: list[Path] = []
    try:
        authorization, receipt, closure, tracked = close(
            args.campaign,
            args.assembly_request,
        )
        campaign = _load_object(args.campaign, "execution campaign")
        artifacts = campaign["artifacts"]
        authorization_path = Path(artifacts["preapproval_authorization"]).resolve()
        receipt_path = Path(artifacts["preapproval_assembly_receipt"]).resolve()
        closure_path = Path(artifacts["execution_closure"]).resolve()
        _atomic_write(authorization_path, _payload(authorization))
        created_outputs.append(authorization_path)
        persisted = _load_object(authorization_path, "persisted preapproval authorization")
        persisted_result = evaluate(
            persisted,
            authorization_path=authorization_path,
            approval_policy_path=Path(closure["approval_policy"]["path"]),
            now=_parse_time(closure["closed_at"], "closed_at"),
        )
        if not (
            persisted_result.get("campaign_stage") == "APPROVAL_COLLECTION"
            and persisted_result.get("evidence_ready_for_approval") is True
            and persisted_result.get("failed_foundation_checks") == []
            and persisted_result.get("failed_evidence_checks") == []
        ):
            raise ValueError("persisted preapproval authorization did not re-verify")
        if any(_sha256(path) != digest for path, digest in tracked.items()):
            raise ValueError("campaign input changed before closure persistence")

        receipt["evaluation"] = persisted_result
        receipt["authorization"] = {
            "path": str(authorization_path),
            "sha256": _sha256(authorization_path),
        }
        _atomic_write(receipt_path, _payload(receipt))
        created_outputs.append(receipt_path)
        closure["evaluation"] = persisted_result
        closure["outputs"] = {
            "preapproval_authorization": receipt["authorization"],
            "preapproval_assembly_receipt": {
                "path": str(receipt_path),
                "sha256": _sha256(receipt_path),
            },
        }
        if any(_sha256(path) != digest for path, digest in tracked.items()):
            raise ValueError("campaign input changed before closure receipt emission")
        _atomic_write(closure_path, _payload(closure))
        created_outputs.append(closure_path)
        if _load_object(closure_path, "persisted execution closure") != closure:
            raise ValueError("persisted execution closure did not re-verify")
    except (OSError, UnicodeError, ValueError) as exc:
        for output in created_outputs:
            output.unlink(missing_ok=True)
        print(f"GA execution campaign closure failed: {exc}", file=sys.stderr)
        return 3
    print(_payload(closure).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
