#!/usr/bin/env python3
"""Inspect partial DuckDock GA execution evidence without authorizing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.archive_ga_authorized_bundle import PRIVATE_KEY_MARKERS, _iter_references
    from scripts.close_ga_execution_campaign import (
        CLOSURE_OUTPUT_KEYS,
        MAX_FILE_BYTES,
        MAX_TOTAL_BYTES,
        _checked_reference,
        _topology_inputs,
        verify_persisted_closure,
    )
    from scripts.prepare_ga_execution_campaign import (
        PLAN_SCHEMA_VERSION,
        _atomic_write,
        _json_payload,
        _load_object,
        _parse_time,
        _sha256,
        prepare,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from archive_ga_authorized_bundle import PRIVATE_KEY_MARKERS, _iter_references
    from close_ga_execution_campaign import (
        CLOSURE_OUTPUT_KEYS,
        MAX_FILE_BYTES,
        MAX_TOTAL_BYTES,
        _checked_reference,
        _topology_inputs,
        verify_persisted_closure,
    )
    from prepare_ga_execution_campaign import (
        PLAN_SCHEMA_VERSION,
        _atomic_write,
        _json_payload,
        _load_object,
        _parse_time,
        _sha256,
        prepare,
    )


PROGRESS_SCHEMA_VERSION = "duckdock-ga-execution-campaign-progress-v1"
AUTHORIZATION_BOUNDARY = "does_not_authorize_GA_or_target_mutation_or_evidence_PASS"
CAMPAIGN_STATUS = "PLANNED_EXTERNAL_EXECUTION"
CAMPAIGN_BOUNDARY = "does_not_authorize_GA_or_target_mutation"
INVALID_STATUSES = {
    "EXPIRED_INCOMPLETE",
    "INVALID_EXTERNAL_EVIDENCE",
    "INVALID_CLOSURE_OUTPUTS",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _absolute_recorded_path(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return Path(os.path.abspath(candidate))


def _fingerprint(path: Path) -> tuple[Any, ...]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    common = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    if stat.S_ISLNK(metadata.st_mode):
        return ("symlink", *common, os.readlink(path))
    if stat.S_ISREG(metadata.st_mode):
        return ("file", *common)
    return ("other", *common)


def _verified_campaign(
    campaign_path: Path,
    assembly_request_path: Path,
) -> tuple[dict[str, Any], Path, Path, dict[Path, str], Path]:
    campaign = _load_object(campaign_path, "execution campaign")
    if not (
        campaign.get("schema_version") == PLAN_SCHEMA_VERSION
        and campaign.get("status") == CAMPAIGN_STATUS
        and campaign.get("authorization_boundary") == CAMPAIGN_BOUNDARY
    ):
        raise ValueError("execution campaign status or authorization boundary is invalid")
    campaign_request_path, _ = _checked_reference(
        campaign.get("request"), label="execution campaign request"
    )
    topology = campaign.get("trust_topology")
    if not isinstance(topology, dict):
        raise ValueError("execution campaign has no trust topology")
    topology_receipt_path, _ = _checked_reference(
        topology.get("receipt"), label="trust topology receipt"
    )
    checked_assembly_path, _ = _checked_reference(
        campaign.get("preapproval_assembly_request"),
        label="preapproval assembly request",
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
    topology_receipt = _load_object(topology_receipt_path, "trust topology receipt")
    topology_inputs, approval_policy_path, _ = _topology_inputs(topology_receipt)
    return (
        campaign,
        campaign_request_path,
        topology_receipt_path,
        topology_inputs,
        approval_policy_path,
    )


def _resolve_raw_reference(
    raw_path: str,
    *,
    authorization_path: Path,
    allowed_paths: set[Path],
) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return _absolute_recorded_path(candidate)
    adjacent = _absolute_recorded_path(authorization_path.parent / candidate)
    cwd_relative = _absolute_recorded_path(Path.cwd() / candidate)
    if adjacent in allowed_paths or cwd_relative not in allowed_paths:
        return adjacent
    return cwd_relative


def _artifact_record(path: Path) -> tuple[dict[str, Any], tuple[Any, ...]]:
    before = _fingerprint(path)
    record: dict[str, Any] = {
        "path": str(path),
        "state": "MISSING",
        "size_bytes": None,
        "sha256": None,
        "reference_count": 0,
        "problems": [],
    }
    if before[0] == "missing":
        return record, before
    if before[0] == "symlink":
        record["state"] = "INVALID"
        record["problems"] = ["symbolic_link_forbidden"]
        return record, before
    if before[0] != "file":
        record["state"] = "INVALID"
        record["problems"] = ["regular_file_required"]
        return record, before
    size = path.stat().st_size
    record["size_bytes"] = size
    if size <= 0:
        record["state"] = "INVALID"
        record["problems"] = ["empty_artifact_forbidden"]
        return record, before
    if size > MAX_FILE_BYTES:
        record["state"] = "INVALID"
        record["problems"] = ["artifact_exceeds_max_file_bytes"]
        return record, before
    payload = path.read_bytes()
    after = _fingerprint(path)
    if after != before or len(payload) != size:
        raise ValueError(f"campaign artifact changed while being inspected: {path}")
    record["sha256"] = _sha256_bytes(payload)
    problems: list[str] = []
    if any(marker in payload for marker in PRIVATE_KEY_MARKERS):
        problems.append("private_key_material_forbidden")
    if path.name.endswith(".json"):
        try:
            parsed = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError):
            problems.append("invalid_json")
            parsed = None
        if parsed is not None and not isinstance(parsed, (dict, list)):
            problems.append("invalid_json_root")
        elif parsed is not None:
            record["parsed_json"] = parsed
    record["state"] = "INVALID" if problems else "PRESENT"
    record["problems"] = problems
    return record, before


def _validate_references(
    records: dict[str, dict[str, Any]],
    *,
    artifacts: dict[str, Path],
    fixed_digests: dict[Path, str],
    authorization_path: Path,
) -> None:
    names_by_path = {path: name for name, path in artifacts.items()}
    allowed_paths = set(names_by_path) | set(fixed_digests)
    artifact_edges: dict[str, list[tuple[str, str, str]]] = {}
    for name, record in records.items():
        parsed = record.pop("parsed_json", None)
        if parsed is None:
            continue
        source_path = artifacts[name]
        references = list(_iter_references(parsed))
        record["reference_count"] = len(references)
        for reference_type, raw_path, declared_digest in references:
            target = _resolve_raw_reference(
                raw_path,
                authorization_path=authorization_path,
                allowed_paths=allowed_paths,
            )
            target_name = names_by_path.get(target)
            if target not in allowed_paths:
                record["problems"].append(
                    f"unplanned_reference:{reference_type}:{raw_path}"
                )
                continue
            if target == source_path:
                record["problems"].append(f"self_reference:{reference_type}:{raw_path}")
                continue
            if target_name is not None:
                target_record = records[target_name]
                artifact_edges.setdefault(name, []).append(
                    (reference_type, raw_path, target_name)
                )
                actual_digest = target_record["sha256"]
            else:
                actual_digest = fixed_digests[target]
            if (
                declared_digest is not None
                and actual_digest is not None
                and actual_digest != declared_digest
            ):
                record["problems"].append(
                    f"reference_digest_mismatch:{reference_type}:{raw_path}"
                )
        if record["problems"]:
            record["state"] = "INVALID"
    changed = True
    while changed:
        changed = False
        for source_name, edges in artifact_edges.items():
            source_record = records[source_name]
            for reference_type, _raw_path, target_name in edges:
                if records[target_name]["state"] == "PRESENT":
                    continue
                problem = (
                    f"referenced_artifact_not_valid:{reference_type}:{target_name}"
                )
                if problem not in source_record["problems"]:
                    source_record["problems"].append(problem)
                    source_record["state"] = "INVALID"
                    changed = True


def _phase_progress(
    campaign: dict[str, Any],
    records: dict[str, dict[str, Any]],
    *,
    closure_state: str,
) -> list[dict[str, Any]]:
    progress: list[dict[str, Any]] = []
    states: dict[str, str] = {}
    phases = campaign.get("phases")
    if not isinstance(phases, list):
        raise ValueError("execution campaign phases must be an array")
    for phase in phases:
        if not isinstance(phase, dict):
            raise ValueError("execution campaign phase must be an object")
        phase_id = str(phase.get("phase_id", ""))
        outputs = phase.get("outputs")
        dependencies = phase.get("depends_on")
        if not isinstance(outputs, list) or not isinstance(dependencies, list):
            raise ValueError(f"execution campaign phase is invalid: {phase_id}")
        if phase_id == "preapproval_assembly":
            if closure_state == "CLOSED":
                state = "CLOSED"
            elif closure_state == "INVALID":
                state = "INVALID"
            elif all(states.get(dependency) == "ARTIFACTS_READY" for dependency in dependencies):
                state = "READY_TO_CLOSE"
            else:
                state = "BLOCKED_BY_EXTERNAL_PHASES"
            missing_outputs = list(outputs) if closure_state == "MISSING" else []
            invalid_outputs: list[str] = []
            present_outputs = [] if closure_state == "MISSING" else list(outputs)
        else:
            output_records = [records[str(output)] for output in outputs]
            missing_outputs = [
                str(output)
                for output, record in zip(outputs, output_records, strict=True)
                if record["state"] == "MISSING"
            ]
            invalid_outputs = [
                str(output)
                for output, record in zip(outputs, output_records, strict=True)
                if record["state"] == "INVALID"
            ]
            present_outputs = [
                str(output)
                for output, record in zip(outputs, output_records, strict=True)
                if record["state"] == "PRESENT"
            ]
            dependencies_ready = all(
                states.get(str(dependency)) == "ARTIFACTS_READY"
                for dependency in dependencies
            )
            if invalid_outputs:
                state = "INVALID"
            elif not missing_outputs and dependencies_ready:
                state = "ARTIFACTS_READY"
            elif present_outputs and not dependencies_ready:
                state = "DEPENDENCY_BLOCKED_WITH_OUTPUTS"
            elif present_outputs:
                state = "PARTIAL"
            elif dependencies_ready:
                state = "PENDING"
            else:
                state = "BLOCKED_BY_DEPENDENCIES"
        states[phase_id] = state
        progress.append(
            {
                "phase_id": phase_id,
                "state": state,
                "risk_class": phase.get("risk_class"),
                "depends_on": dependencies,
                "tools": phase.get("tools"),
                "authorized_policy_roles": phase.get("authorized_policy_roles"),
                "required_acknowledgement": phase.get("required_acknowledgement"),
                "present_outputs": present_outputs,
                "missing_outputs": missing_outputs,
                "invalid_outputs": invalid_outputs,
            }
        )
    return progress


def _next_action(
    status: str,
    phases: list[dict[str, Any]],
    *,
    campaign_path: Path,
    assembly_request_path: Path,
    invalid_artifacts: list[str],
) -> dict[str, Any]:
    if status == "CAMPAIGN_CLOSED":
        return {"code": "freeze_preapproval_campaign", "phase_id": "preapproval_assembly"}
    if status == "EXPIRED_INCOMPLETE":
        return {"code": "abandon_campaign_and_prepare_fresh_campaign", "phase_id": None}
    if status == "INVALID_CLOSURE_OUTPUTS":
        return {"code": "quarantine_partial_or_invalid_closure_outputs", "phase_id": "preapproval_assembly"}
    if status == "INVALID_EXTERNAL_EVIDENCE":
        return {
            "code": "replace_invalid_external_artifacts",
            "phase_id": None,
            "invalid_artifacts": invalid_artifacts,
        }
    if status == "READY_FOR_CLOSURE_ATTEMPT":
        return {
            "code": "run_fail_closed_campaign_closure",
            "phase_id": "preapproval_assembly",
            "tool": "close_ga_execution_campaign.py",
            "arguments": {
                "campaign": str(campaign_path),
                "assembly_request": str(assembly_request_path),
            },
        }
    if status == "WAITING_FOR_EXECUTION_WINDOW":
        return {"code": "wait_for_execution_window", "phase_id": None}
    for phase in phases:
        if phase["phase_id"] == "preapproval_assembly":
            continue
        if phase["state"] in {"PENDING", "PARTIAL", "DEPENDENCY_BLOCKED_WITH_OUTPUTS"}:
            return {
                "code": "continue_external_phase",
                "phase_id": phase["phase_id"],
                "tools": phase["tools"],
                "authorized_policy_roles": phase["authorized_policy_roles"],
                "required_acknowledgement": phase["required_acknowledgement"],
                "missing_outputs": phase["missing_outputs"],
            }
    return {"code": "resolve_phase_dependencies", "phase_id": None}


def inspect(
    campaign_path: Path,
    assembly_request_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for path, label in (
        (campaign_path, "execution campaign"),
        (assembly_request_path, "preapproval assembly request"),
    ):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    campaign_path = campaign_path.resolve()
    assembly_request_path = assembly_request_path.resolve()
    campaign_digest = _sha256(campaign_path)
    assembly_digest = _sha256(assembly_request_path)
    (
        campaign,
        campaign_request_path,
        topology_receipt_path,
        topology_inputs,
        approval_policy_path,
    ) = _verified_campaign(campaign_path, assembly_request_path)

    starts_at = _parse_time(campaign.get("window_starts_at"), "window_starts_at")
    expires_at = _parse_time(campaign.get("window_expires_at"), "window_expires_at")
    if current < _parse_time(campaign.get("created_at"), "campaign created_at") - timedelta(minutes=5):
        raise ValueError("inspection time precedes campaign creation")
    if current < starts_at:
        window_state = "NOT_STARTED"
    elif current > expires_at:
        window_state = "EXPIRED"
    else:
        window_state = "ACTIVE"

    artifacts_value = campaign.get("artifacts")
    if not isinstance(artifacts_value, dict):
        raise ValueError("execution campaign artifacts must be an object")
    all_artifacts = {
        str(name): _absolute_recorded_path(Path(str(path)))
        for name, path in artifacts_value.items()
    }
    if CLOSURE_OUTPUT_KEYS - set(all_artifacts):
        raise ValueError("execution campaign is missing closure outputs")
    external_artifacts = {
        name: path
        for name, path in all_artifacts.items()
        if name not in CLOSURE_OUTPUT_KEYS
    }
    fingerprints: dict[Path, tuple[Any, ...]] = {}
    records: dict[str, dict[str, Any]] = {}
    for name, path in sorted(external_artifacts.items()):
        record, fingerprint = _artifact_record(path)
        records[name] = record
        fingerprints[path] = fingerprint

    topology = campaign["trust_topology"]
    _, topology_receipt_digest = _checked_reference(
        topology["receipt"], label="trust topology receipt"
    )
    request_reference = campaign["request"]
    _, request_digest = _checked_reference(
        request_reference, label="execution campaign request"
    )
    execution = campaign.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("execution campaign has no execution controls")
    backup_path, backup_digest = _checked_reference(
        execution.get("backup_allowed_signers"),
        label="campaign backup allowed-signers",
    )
    fixed_digests = {
        campaign_path: campaign_digest,
        campaign_request_path: request_digest,
        assembly_request_path: assembly_digest,
        topology_receipt_path: topology_receipt_digest,
        backup_path: backup_digest,
        **topology_inputs,
    }
    _validate_references(
        records,
        artifacts=external_artifacts,
        fixed_digests=fixed_digests,
        authorization_path=all_artifacts["preapproval_authorization"],
    )

    total_present_bytes = sum(
        int(record["size_bytes"] or 0)
        for record in records.values()
        if record["state"] != "MISSING"
    )
    global_problems: list[str] = []
    if total_present_bytes > MAX_TOTAL_BYTES:
        global_problems.append("present_artifacts_exceed_max_total_bytes")

    closure_fingerprints = {
        name: _fingerprint(all_artifacts[name])
        for name in sorted(CLOSURE_OUTPUT_KEYS)
    }
    closure_present = [
        name for name, fingerprint in closure_fingerprints.items() if fingerprint[0] != "missing"
    ]
    closure_state = "MISSING"
    closure_validation: dict[str, Any] | None = None
    if closure_present:
        if len(closure_present) != len(CLOSURE_OUTPUT_KEYS) or any(
            fingerprint[0] != "file" for fingerprint in closure_fingerprints.values()
        ):
            closure_state = "INVALID"
            global_problems.append("closure_outputs_are_partial_or_non_regular")
        else:
            try:
                closure_validation = verify_persisted_closure(
                    all_artifacts["execution_closure"],
                    authorization_path=all_artifacts["preapproval_authorization"],
                    approval_policy_path=approval_policy_path,
                    now=current,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                closure_state = "INVALID"
                global_problems.append(f"persisted_closure_invalid:{exc}")
            else:
                closure_state = "CLOSED"

    phases = _phase_progress(campaign, records, closure_state=closure_state)
    invalid_artifacts = sorted(
        name for name, record in records.items() if record["state"] == "INVALID"
    )
    missing_artifacts = sorted(
        name for name, record in records.items() if record["state"] == "MISSING"
    )
    present_artifacts = sorted(
        name for name, record in records.items() if record["state"] == "PRESENT"
    )
    external_phases = [phase for phase in phases if phase["phase_id"] != "preapproval_assembly"]
    all_external_phases_ready = all(
        phase["state"] == "ARTIFACTS_READY" for phase in external_phases
    )
    if closure_state == "CLOSED":
        status = "CAMPAIGN_CLOSED"
    elif closure_state == "INVALID":
        status = "INVALID_CLOSURE_OUTPUTS"
    elif window_state == "EXPIRED":
        status = "EXPIRED_INCOMPLETE"
    elif invalid_artifacts or global_problems:
        status = "INVALID_EXTERNAL_EVIDENCE"
    elif window_state == "NOT_STARTED":
        status = "WAITING_FOR_EXECUTION_WINDOW"
    elif all_external_phases_ready and not missing_artifacts:
        status = "READY_FOR_CLOSURE_ATTEMPT"
    else:
        status = "EXTERNAL_EVIDENCE_IN_PROGRESS"

    if _sha256(campaign_path) != campaign_digest or _sha256(assembly_request_path) != assembly_digest:
        raise ValueError("campaign metadata changed during progress inspection")
    for path, fingerprint in {**fingerprints, **{all_artifacts[name]: value for name, value in closure_fingerprints.items()}}.items():
        if _fingerprint(path) != fingerprint:
            raise ValueError(f"campaign artifact changed during progress inspection: {path}")

    progress = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "status": status,
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "observed_at": current.isoformat(),
        "campaign": {
            "path": str(campaign_path),
            "sha256": campaign_digest,
            "campaign_id": campaign["campaign_id"],
        },
        "assembly_request": {
            "path": str(assembly_request_path),
            "sha256": assembly_digest,
        },
        "window": {
            "state": window_state,
            "starts_at": starts_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        "counts": {
            "planned_external_artifacts": len(external_artifacts),
            "present_valid_artifacts": len(present_artifacts),
            "missing_artifacts": len(missing_artifacts),
            "invalid_artifacts": len(invalid_artifacts),
            "present_size_bytes": total_present_bytes,
            "external_phases": len(external_phases),
            "ready_external_phases": sum(
                phase["state"] == "ARTIFACTS_READY" for phase in external_phases
            ),
        },
        "missing_artifacts": missing_artifacts,
        "invalid_artifacts": invalid_artifacts,
        "global_problems": global_problems,
        "artifact_progress": records,
        "phase_progress": phases,
        "closure": {
            "state": closure_state,
            "outputs_present": closure_present,
            "closure_sha256": (
                closure_validation.get("closure_sha256")
                if closure_validation is not None
                else None
            ),
        },
        "next_action": _next_action(
            status,
            phases,
            campaign_path=campaign_path,
            assembly_request_path=assembly_request_path,
            invalid_artifacts=invalid_artifacts,
        ),
    }
    return progress


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--assembly-request", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--observed-at")
    parser.add_argument(
        "--require-ready-for-closure",
        action="store_true",
        help="exit 2 unless the campaign is ready for closure or already closed",
    )
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.campaign, "execution campaign"),
            (args.assembly_request, "preapproval assembly request"),
        ):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} must be a regular non-symlink file: {path}")
        if args.output is not None:
            if args.output.exists():
                raise ValueError("progress output already exists; checkpoints are immutable")
            raw_campaign = _load_object(args.campaign, "execution campaign")
            execution = raw_campaign.get("execution")
            raw_root = execution.get("evidence_root") if isinstance(execution, dict) else None
            if isinstance(raw_root, str) and raw_root.strip():
                evidence_root = Path(raw_root).expanduser().resolve()
                output = args.output.resolve()
                if output == evidence_root or evidence_root in output.parents:
                    raise ValueError("progress output must remain outside the exact evidence root")
        if args.observed_at is not None:
            _parse_time(args.observed_at, "observed_at")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    observed_at = _parse_time(args.observed_at, "observed_at") if args.observed_at else None
    created_output = False
    try:
        result = inspect(args.campaign, args.assembly_request, now=observed_at)
        payload = _json_payload(result)
        if args.output is not None:
            _atomic_write(args.output, payload)
            created_output = True
            if _load_object(args.output, "persisted campaign progress") != result:
                raise ValueError("persisted campaign progress did not re-open exactly")
    except (OSError, UnicodeError, ValueError) as exc:
        if created_output and args.output is not None:
            args.output.unlink(missing_ok=True)
        print(f"GA execution campaign inspection failed: {exc}", file=sys.stderr)
        return 3
    print(payload.decode("utf-8"), end="")
    if result["status"] in INVALID_STATUSES:
        return 2
    if args.require_ready_for_closure and result["status"] not in {
        "READY_FOR_CLOSURE_ATTEMPT",
        "CAMPAIGN_CLOSED",
    }:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
