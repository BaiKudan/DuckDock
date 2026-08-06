#!/usr/bin/env python3
"""Independently verify and re-evaluate a portable DuckDock GA archive."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

try:
    from scripts.archive_ga_authorized_bundle import (
        ARCHIVE_MANIFEST_SCHEMA_VERSION,
        PRIVATE_KEY_MARKERS,
        ROOT_LABEL_PATTERN,
        SHA256_PATTERN,
        _iter_references,
        _sha256_bytes,
        _verify_archive_payload,
    )
    from scripts.ga_path_resolution import ga_file_resolution_overrides
    from scripts.verify_ga_production_authorization import evaluate
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from archive_ga_authorized_bundle import (
        ARCHIVE_MANIFEST_SCHEMA_VERSION,
        PRIVATE_KEY_MARKERS,
        ROOT_LABEL_PATTERN,
        SHA256_PATTERN,
        _iter_references,
        _sha256_bytes,
        _verify_archive_payload,
    )
    from ga_path_resolution import ga_file_resolution_overrides
    from verify_ga_production_authorization import evaluate


VERIFICATION_SCHEMA_VERSION = "duckdock-ga-authorized-archive-verification-v1"
ARCHIVE_PATH_PATTERN = re.compile(
    r"^artifacts/(?P<sha256>[0-9a-f]{64})/(?P<name>[A-Za-z0-9._-]{1,80})$"
)
FILE_RECORD_KEYS = {
    "archive_path",
    "sha256",
    "size_bytes",
    "source_relative_path",
    "source_root",
}
REFERENCE_RECORD_KEYS = {
    "source_archive_path",
    "target_archive_path",
    "reference_type",
    "raw_path",
    "declared_sha256",
}
REFERENCE_TYPES = {
    "content_sha256",
    "content_manifest_sha256",
    "allowed_signers_sha256",
    "signature",
}
EVALUATION_KEYS = {
    "status",
    "campaign_stage",
    "foundation_ready",
    "evidence_ready_for_approval",
    "approvals_complete",
    "block_count",
    "checked_at",
}
MANIFEST_KEYS = {
    "schema_version",
    "created_at",
    "release_digest",
    "authorization",
    "approval_policy",
    "supplemental_files",
    "allowed_roots",
    "references",
    "evaluation",
    "file_count",
    "total_size_bytes",
    "files",
}
DEFAULT_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be RFC3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be RFC3339: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _read_bounded(path: Path, *, maximum: int, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    if path.stat().st_size > maximum:
        raise ValueError(f"{label} exceeds its byte limit: {path}")
    payload = path.read_bytes()
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds its byte limit: {path}")
    return payload


def _load_manifest(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    return value


def _expected_digest(args: argparse.Namespace, archive_name: str) -> str:
    if args.expected_sha256:
        if not SHA256_PATTERN.fullmatch(args.expected_sha256):
            raise ValueError("expected-sha256 must be 64 lowercase hexadecimal characters")
        return args.expected_sha256
    payload = _read_bounded(
        args.digest,
        maximum=4096,
        label="archive digest sidecar",
    )
    try:
        line = payload.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"archive digest sidecar is not UTF-8: {exc}") from exc
    if not line.endswith("\n") or line.count("\n") != 1:
        raise ValueError("archive digest sidecar must contain exactly one newline-terminated record")
    digest, separator, filename = line[:-1].partition("  ")
    if (
        not separator
        or not SHA256_PATTERN.fullmatch(digest)
        or filename != archive_name
    ):
        raise ValueError("archive digest sidecar does not match the archive filename")
    return digest


def _relative_source_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("file source_relative_path must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"file source_relative_path is unsafe: {value}")
    return value


def _validate_file_record(
    value: Any,
    *,
    roots: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FILE_RECORD_KEYS:
        raise ValueError("manifest file record has invalid fields")
    archive_path = value.get("archive_path")
    digest = value.get("sha256")
    size = value.get("size_bytes")
    source_root = value.get("source_root")
    match = ARCHIVE_PATH_PATTERN.fullmatch(archive_path) if isinstance(archive_path, str) else None
    if match is None or not isinstance(digest, str) or match.group("sha256") != digest:
        raise ValueError("manifest file archive path is not bound to its SHA-256")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("manifest file size_bytes must be a non-negative integer")
    if not isinstance(source_root, str) or source_root not in roots:
        raise ValueError("manifest file source_root is not declared")
    _relative_source_path(value.get("source_relative_path"))
    return value


def _validate_manifest(
    manifest: dict[str, Any],
    *,
    max_total_bytes: int,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    datetime,
]:
    if manifest.get("schema_version") != ARCHIVE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported GA archive manifest schema")
    if set(manifest) != MANIFEST_KEYS:
        raise ValueError("GA archive manifest has invalid top-level fields")
    roots_value = manifest.get("allowed_roots")
    if (
        not isinstance(roots_value, list)
        or not roots_value
        or any(
            not isinstance(root, str) or not ROOT_LABEL_PATTERN.fullmatch(root)
            for root in roots_value
        )
        or len(set(roots_value)) != len(roots_value)
    ):
        raise ValueError("manifest allowed_roots must contain unique safe labels")
    roots = set(roots_value)

    files_value = manifest.get("files")
    if not isinstance(files_value, list) or not files_value:
        raise ValueError("manifest files must be a non-empty array")
    files = [_validate_file_record(value, roots=roots) for value in files_value]
    logical_sources = [
        (record["source_root"], record["source_relative_path"]) for record in files
    ]
    if len(set(logical_sources)) != len(logical_sources):
        raise ValueError("manifest contains duplicate logical source files")
    archive_metadata: dict[str, tuple[str, int]] = {}
    for record in files:
        metadata = (record["sha256"], record["size_bytes"])
        prior = archive_metadata.setdefault(record["archive_path"], metadata)
        if prior != metadata:
            raise ValueError("manifest has conflicting metadata for one archive member")
    if manifest.get("file_count") != len(files):
        raise ValueError("manifest file_count does not match files")
    total_size = sum(record["size_bytes"] for record in files)
    if manifest.get("total_size_bytes") != total_size or total_size > max_total_bytes:
        raise ValueError("manifest total_size_bytes is invalid or exceeds the verifier limit")

    authorization = _validate_file_record(manifest.get("authorization"), roots=roots)
    approval_policy = _validate_file_record(manifest.get("approval_policy"), roots=roots)
    if authorization not in files or approval_policy not in files:
        raise ValueError("manifest authorization or approval policy is not in files")
    if authorization["archive_path"] == approval_policy["archive_path"]:
        raise ValueError("manifest authorization and approval policy must be distinct")
    supplemental_value = manifest.get("supplemental_files")
    if not isinstance(supplemental_value, list):
        raise ValueError("manifest supplemental_files must be an array")
    supplemental = [
        _validate_file_record(value, roots=roots) for value in supplemental_value
    ]
    if any(record not in files for record in supplemental):
        raise ValueError("manifest supplemental file is not in files")
    supplemental_paths = [record["archive_path"] for record in supplemental]
    if len(set(supplemental_paths)) != len(supplemental_paths):
        raise ValueError("manifest contains duplicate supplemental files")

    evaluation = manifest.get("evaluation")
    if not isinstance(evaluation, dict) or set(evaluation) != EVALUATION_KEYS:
        raise ValueError("manifest evaluation has invalid fields")
    if not (
        evaluation.get("status") == "GA_AUTHORIZED"
        and evaluation.get("campaign_stage") == "AUTHORIZED"
        and evaluation.get("foundation_ready") is True
        and evaluation.get("evidence_ready_for_approval") is True
        and evaluation.get("approvals_complete") is True
        and evaluation.get("block_count") == 0
    ):
        raise ValueError("manifest does not record an unblocked GA_AUTHORIZED result")
    checked_at = _parse_time(evaluation.get("checked_at"), "evaluation.checked_at")
    created_at = _parse_time(manifest.get("created_at"), "created_at")
    if checked_at != created_at:
        raise ValueError("manifest canonical evaluation time must equal created_at")
    if checked_at > datetime.now(timezone.utc) + timedelta(seconds=300):
        raise ValueError("manifest canonical evaluation time is in the future")
    if not SHA256_PATTERN.fullmatch(str(manifest.get("release_digest", ""))):
        raise ValueError("manifest release_digest is invalid")

    paths = {record["archive_path"] for record in files}
    references_value = manifest.get("references")
    if not isinstance(references_value, list) or not references_value:
        raise ValueError("manifest references must be a non-empty array")
    references: list[dict[str, Any]] = []
    reference_keys: set[tuple[str, str, str, str | None]] = set()
    raw_targets: dict[str, str] = {}
    targets: set[str] = set()
    records_by_path = {record["archive_path"]: record for record in files}
    for value in references_value:
        if not isinstance(value, dict) or set(value) != REFERENCE_RECORD_KEYS:
            raise ValueError("manifest reference record has invalid fields")
        source = value.get("source_archive_path")
        target = value.get("target_archive_path")
        reference_type = value.get("reference_type")
        raw_path = value.get("raw_path")
        declared = value.get("declared_sha256")
        if source not in paths or target not in paths:
            raise ValueError("manifest reference points outside the file set")
        if reference_type not in REFERENCE_TYPES:
            raise ValueError("manifest reference_type is unsupported")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("manifest reference raw_path must be non-empty")
        if reference_type == "signature":
            if declared is not None:
                raise ValueError("signature reference must not declare a content digest")
        elif (
            not isinstance(declared, str)
            or not SHA256_PATTERN.fullmatch(declared)
            or records_by_path[target]["sha256"] != declared
        ):
            raise ValueError("content reference digest does not match its target")
        key = (source, reference_type, raw_path, declared)
        if key in reference_keys:
            raise ValueError("manifest contains a duplicate reference record")
        reference_keys.add(key)
        prior = raw_targets.get(raw_path)
        if prior is not None and prior != target:
            raise ValueError("one raw evidence path maps to multiple archive members")
        raw_targets[raw_path] = target
        targets.add(target)
        references.append(value)

    seeds = {
        authorization["archive_path"],
        approval_policy["archive_path"],
        *(record["archive_path"] for record in supplemental),
    }
    if any(record["archive_path"] not in seeds | targets for record in files):
        raise ValueError("manifest contains an unreferenced non-seed file")
    return files, references, checked_at


def _archive_members(archive_payload: bytes) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.name == "manifest.json":
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"archive member is unreadable: {member.name}")
            payload = extracted.read()
            if any(marker in payload for marker in PRIVATE_KEY_MARKERS):
                raise ValueError(f"archive contains forbidden private-key material: {member.name}")
            members[member.name] = payload
    return members


def _validate_reference_index(
    *,
    files: Sequence[dict[str, Any]],
    references: Sequence[dict[str, Any]],
    members: dict[str, bytes],
) -> None:
    declared_records = {
        (
            reference["source_archive_path"],
            reference["reference_type"],
            reference["raw_path"],
            reference["declared_sha256"],
        )
        for reference in references
    }
    discovered_records: set[tuple[str, str, str, str | None]] = set()
    for source, payload in members.items():
        try:
            value = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError):
            continue
        for reference_type, raw_path, declared in _iter_references(value):
            discovered_records.add((source, reference_type, raw_path, declared))
    if discovered_records != declared_records:
        raise ValueError("manifest reference index does not exactly match archived JSON")

    records_by_path = {record["archive_path"]: record for record in files}
    for reference in references:
        target = reference["target_archive_path"]
        payload = members[target]
        record = records_by_path[target]
        if len(payload) != record["size_bytes"] or _sha256_bytes(payload) != record["sha256"]:
            raise ValueError(f"reference target content mismatch: {target}")


def _assert_authorized(result: dict[str, Any], manifest: dict[str, Any]) -> None:
    evaluation = manifest["evaluation"]
    if not (
        result.get("status") == "GA_AUTHORIZED"
        and result.get("campaign_stage") == "AUTHORIZED"
        and result.get("foundation_ready") is True
        and result.get("evidence_ready_for_approval") is True
        and result.get("approvals_complete") is True
        and result.get("block_count") == 0
        and result.get("failed_foundation_checks") == []
        and result.get("failed_evidence_checks") == []
        and result.get("failed_approval_checks") == []
        and result.get("next_action") == "archive_authorized_bundle"
        and result.get("release_digest") == manifest.get("release_digest")
        and result.get("checked_at") == evaluation.get("checked_at")
    ):
        raise ValueError(
            "archived authorization did not independently re-evaluate as "
            f"GA_AUTHORIZED: status={result.get('status')}, "
            f"foundation={result.get('failed_foundation_checks')}, "
            f"evidence={result.get('failed_evidence_checks')}, "
            f"approvals={result.get('failed_approval_checks')}"
        )


def verify(args: argparse.Namespace) -> dict[str, Any]:
    archive_payload = _read_bounded(
        args.archive,
        maximum=args.max_archive_bytes,
        label="GA archive",
    )
    expected_digest = _expected_digest(args, args.archive.name)
    archive_digest = _sha256_bytes(archive_payload)
    if archive_digest != expected_digest:
        raise ValueError("GA archive SHA-256 does not match the independent expected digest")
    manifest_payload = _read_bounded(
        args.manifest,
        maximum=args.max_manifest_bytes,
        label="GA archive manifest",
    )
    manifest = _load_manifest(manifest_payload)
    files, references, checked_at = _validate_manifest(
        manifest,
        max_total_bytes=args.max_total_bytes,
    )
    _verify_archive_payload(archive_payload, manifest_payload)
    members = _archive_members(archive_payload)
    _validate_reference_index(
        files=files,
        references=references,
        members=members,
    )

    with tempfile.TemporaryDirectory(prefix="duckdock-ga-archive-verify-") as directory:
        root = Path(directory)
        materialized: dict[str, Path] = {}
        for archive_path, payload in members.items():
            target = root / archive_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            materialized[archive_path] = target
        overrides = {
            reference["raw_path"]: materialized[reference["target_archive_path"]]
            for reference in references
        }
        authorization_path = materialized[manifest["authorization"]["archive_path"]]
        approval_policy_path = materialized[manifest["approval_policy"]["archive_path"]]
        try:
            authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"archived authorization is not valid JSON: {exc}") from exc
        if not isinstance(authorization, dict):
            raise ValueError("archived authorization root must be an object")
        with ga_file_resolution_overrides(overrides, strict=True):
            result = evaluate(
                authorization,
                authorization_path=authorization_path,
                approval_policy_path=approval_policy_path,
                now=checked_at,
            )
        _assert_authorized(result, manifest)

    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "status": "GA_AUTHORIZED_ARCHIVE_VERIFIED",
        "archive_sha256": archive_digest,
        "manifest_sha256": _sha256_bytes(manifest_payload),
        "release_digest": result["release_digest"],
        "checked_at": result["checked_at"],
        "file_count": manifest["file_count"],
        "reference_count": len(references),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    digest_group = parser.add_mutually_exclusive_group(required=True)
    digest_group.add_argument("--digest", type=Path)
    digest_group.add_argument("--expected-sha256")
    parser.add_argument("--max-archive-bytes", type=int, default=DEFAULT_MAX_ARCHIVE_BYTES)
    parser.add_argument("--max-manifest-bytes", type=int, default=DEFAULT_MAX_MANIFEST_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    args = parser.parse_args(argv)
    try:
        if args.max_archive_bytes <= 0 or args.max_manifest_bytes <= 0 or args.max_total_bytes <= 0:
            raise ValueError("verifier byte limits must be positive")
        for path, label in ((args.archive, "archive"), (args.manifest, "manifest")):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} must be a regular non-symlink file: {path}")
        if args.digest is not None and (args.digest.is_symlink() or not args.digest.is_file()):
            raise ValueError(f"digest must be a regular non-symlink file: {args.digest}")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify(args)
    except (OSError, UnicodeError, ValueError, tarfile.TarError) as exc:
        print(f"GA authorized archive verification failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
