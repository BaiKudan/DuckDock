#!/usr/bin/env python3
"""Create an immutable deterministic archive from a GA_AUTHORIZED bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from scripts.ga_approval_campaign import require_formal_campaign_freeze
    from scripts.ga_path_resolution import (
        ga_file_resolution_override,
        ga_file_resolution_overrides,
    )
    from scripts.verify_ga_production_authorization import evaluate
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_approval_campaign import require_formal_campaign_freeze
    from ga_path_resolution import ga_file_resolution_override, ga_file_resolution_overrides
    from verify_ga_production_authorization import evaluate


ARCHIVE_MANIFEST_SCHEMA_VERSION = "duckdock-ga-authorized-archive-manifest-v2"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ROOT_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_CREATED_AT_SKEW_SECONDS = 300
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"AGE-SECRET-KEY-1",
)


@dataclass(frozen=True)
class AllowedRoot:
    label: str
    path: Path


@dataclass(frozen=True)
class CapturedFile:
    path: Path
    source_root: str
    source_relative_path: str
    archive_path: str
    payload: bytes
    sha256: str


@dataclass(frozen=True)
class CapturedReference:
    source: Path
    target: Path
    reference_type: str
    raw_path: str
    declared_sha256: str | None


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"created-at is not RFC3339: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError("created-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _root_for(path: Path, roots: Sequence[AllowedRoot]) -> AllowedRoot:
    matches = [root for root in roots if _is_relative_to(path, root.path)]
    if not matches:
        raise ValueError(f"referenced file is outside every allowed root: {path}")
    return sorted(matches, key=lambda item: (-len(item.path.parts), item.label))[0]


def _resolve_file(
    raw_path: str,
    *,
    authorization_path: Path,
    roots: Sequence[AllowedRoot],
) -> Path:
    handled, overridden = ga_file_resolution_override(raw_path)
    if handled:
        if overridden is None:
            raise ValueError(f"referenced file has no verified portable mapping: {raw_path}")
        candidate = overridden
    else:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            adjacent = authorization_path.parent / candidate
            candidate = adjacent if adjacent.exists() else Path.cwd() / candidate
    if candidate.is_symlink():
        raise ValueError(f"symbolic-link evidence is forbidden: {candidate}")
    resolved = candidate.resolve()
    _root_for(resolved, roots)
    if not resolved.is_file():
        raise ValueError(f"referenced file does not exist: {resolved}")
    return resolved


def _resolve_input_file(path: Path, *, roots: Sequence[AllowedRoot]) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.is_symlink():
        raise ValueError(f"symbolic-link evidence is forbidden: {candidate}")
    resolved = candidate.resolve()
    _root_for(resolved, roots)
    if not resolved.is_file():
        raise ValueError(f"supplemental file does not exist: {resolved}")
    return resolved


def _safe_basename(path: Path) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name).strip("._")
    return (safe or "artifact")[:80]


def _iter_references(value: Any) -> Iterable[tuple[str, str, str | None]]:
    if isinstance(value, dict):
        raw_path = value.get("path")
        declared_digest = value.get("sha256")
        if (
            isinstance(raw_path, str)
            and raw_path.strip()
            and isinstance(declared_digest, str)
            and SHA256_PATTERN.fullmatch(declared_digest)
        ):
            yield "content_sha256", raw_path, declared_digest

        manifest_digest = value.get("manifest_sha256")
        if (
            isinstance(raw_path, str)
            and raw_path.strip()
            and not isinstance(declared_digest, str)
            and isinstance(manifest_digest, str)
            and SHA256_PATTERN.fullmatch(manifest_digest)
        ):
            yield "content_manifest_sha256", raw_path, manifest_digest

        allowed_signers_path = value.get("allowed_signers_path")
        allowed_signers_digest = value.get("allowed_signers_sha256")
        if (
            isinstance(allowed_signers_path, str)
            and allowed_signers_path.strip()
            and isinstance(allowed_signers_digest, str)
            and SHA256_PATTERN.fullmatch(allowed_signers_digest)
        ):
            yield (
                "allowed_signers_sha256",
                allowed_signers_path,
                allowed_signers_digest,
            )

        signature_path = value.get("signature_path")
        if isinstance(signature_path, str) and signature_path.strip():
            yield "signature", signature_path, None

        for nested in value.values():
            yield from _iter_references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_references(nested)


def _json_value(payload: bytes) -> Any | None:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None


def _assert_authorized(result: dict[str, Any]) -> None:
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
    ):
        raise ValueError("authorization did not re-verify as unblocked GA_AUTHORIZED")


def _evaluate(
    authorization_path: Path,
    approval_policy_path: Path,
    *,
    now: datetime,
    allow_legacy_unbound: bool,
) -> dict[str, Any]:
    authorization = _load_object(authorization_path, "GA authorization")
    result = evaluate(
        authorization,
        authorization_path=authorization_path,
        approval_policy_path=approval_policy_path,
        now=now,
    )
    _assert_authorized(result)
    if not allow_legacy_unbound:
        require_formal_campaign_freeze(
            authorization,
            authorization_path=authorization_path,
        )
    return result


def _capture_reference_closure(
    seeds: Sequence[Path],
    *,
    authorization_path: Path,
    roots: Sequence[AllowedRoot],
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[list[CapturedFile], list[CapturedReference]]:
    queue = list(seeds)
    queued = set(seeds)
    captured: dict[Path, CapturedFile] = {}
    declared_digests: dict[Path, str] = {}
    captured_references: list[CapturedReference] = []
    total_bytes = 0

    while queue:
        path = queue.pop(0)
        queued.discard(path)
        if path in captured:
            continue
        if path.is_symlink():
            raise ValueError(f"symbolic-link evidence is forbidden: {path}")
        if not path.is_file():
            raise ValueError(f"referenced file does not exist: {path}")
        if path.stat().st_size > max_file_bytes:
            raise ValueError(f"referenced file exceeds max-file-bytes: {path}")
        payload = path.read_bytes()
        size = len(payload)
        if size > max_file_bytes:
            raise ValueError(f"referenced file exceeds max-file-bytes: {path}")
        total_bytes += size
        if total_bytes > max_total_bytes:
            raise ValueError("referenced evidence exceeds max-total-bytes")
        if any(marker in payload for marker in PRIVATE_KEY_MARKERS):
            raise ValueError(f"private-key material is forbidden in authorized archive: {path}")
        digest = _sha256_bytes(payload)
        if digest != declared_digests.get(path, digest):
            raise ValueError(f"referenced file digest mismatch: {path}")
        root = _root_for(path, roots)
        relative = path.relative_to(root.path).as_posix()
        captured[path] = CapturedFile(
            path=path,
            source_root=root.label,
            source_relative_path=relative,
            archive_path=f"artifacts/{digest}/{_safe_basename(path)}",
            payload=payload,
            sha256=digest,
        )

        parsed = _json_value(payload)
        if parsed is None:
            continue
        references: list[Path] = []
        for reference_type, raw_reference, expected_digest in _iter_references(
            parsed
        ):
            reference = _resolve_file(
                raw_reference,
                authorization_path=authorization_path,
                roots=roots,
            )
            existing_digest = declared_digests.get(reference)
            if (
                expected_digest is not None
                and existing_digest is not None
                and existing_digest != expected_digest
            ):
                raise ValueError(
                    f"conflicting declared digests for referenced file: {reference}"
                )
            if expected_digest is not None:
                declared_digests[reference] = expected_digest
                if (
                    reference in captured
                    and captured[reference].sha256 != expected_digest
                ):
                    raise ValueError(f"referenced file digest mismatch: {reference}")
            captured_references.append(
                CapturedReference(
                    source=path,
                    target=reference,
                    reference_type=reference_type,
                    raw_path=raw_reference,
                    declared_sha256=expected_digest,
                )
            )
            references.append(reference)
        for reference in sorted(set(references), key=str):
            if reference not in captured and reference not in queued:
                queue.append(reference)
                queued.add(reference)

    return (
        sorted(
            captured.values(),
            key=lambda item: (
                item.source_root,
                item.source_relative_path,
                item.sha256,
            ),
        ),
        captured_references,
    )


def _manifest(
    *,
    captured: Sequence[CapturedFile],
    captured_references: Sequence[CapturedReference],
    authorization: Path,
    approval_policy: Path,
    supplemental_files: Sequence[Path],
    result: dict[str, Any],
    roots: Sequence[AllowedRoot],
    created_at: datetime,
) -> dict[str, Any]:
    records = [
        {
            "archive_path": item.archive_path,
            "sha256": item.sha256,
            "size_bytes": len(item.payload),
            "source_relative_path": item.source_relative_path,
            "source_root": item.source_root,
        }
        for item in captured
    ]
    by_path = {
        item.path: record for item, record in zip(captured, records, strict=True)
    }
    reference_records_by_key: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    raw_targets: dict[str, str] = {}
    for reference in captured_references:
        source_archive_path = by_path[reference.source]["archive_path"]
        target_archive_path = by_path[reference.target]["archive_path"]
        prior_target = raw_targets.get(reference.raw_path)
        if prior_target is not None and prior_target != target_archive_path:
            raise ValueError(
                f"one raw evidence path resolves to multiple archive members: {reference.raw_path}"
            )
        raw_targets[reference.raw_path] = target_archive_path
        key = (
            source_archive_path,
            reference.reference_type,
            reference.raw_path,
            reference.declared_sha256,
        )
        reference_records_by_key[key] = {
            "source_archive_path": source_archive_path,
            "target_archive_path": target_archive_path,
            "reference_type": reference.reference_type,
            "raw_path": reference.raw_path,
            "declared_sha256": reference.declared_sha256,
        }
    reference_records = [
        reference_records_by_key[key]
        for key in sorted(
            reference_records_by_key,
            key=lambda item: (item[0], item[1], item[2], item[3] or ""),
        )
    ]
    return {
        "schema_version": ARCHIVE_MANIFEST_SCHEMA_VERSION,
        "created_at": created_at.isoformat(),
        "release_digest": result["release_digest"],
        "authorization": by_path[authorization],
        "approval_policy": by_path[approval_policy],
        "supplemental_files": [by_path[path] for path in supplemental_files],
        "allowed_roots": [root.label for root in roots],
        "references": reference_records,
        "evaluation": {
            "status": result["status"],
            "campaign_stage": result["campaign_stage"],
            "foundation_ready": result["foundation_ready"],
            "evidence_ready_for_approval": result["evidence_ready_for_approval"],
            "approvals_complete": result["approvals_complete"],
            "block_count": result["block_count"],
            "checked_at": result["checked_at"],
        },
        "file_count": len(records),
        "total_size_bytes": sum(record["size_bytes"] for record in records),
        "files": records,
    }


def _tar_entry(name: str, payload: bytes) -> tuple[tarfile.TarInfo, io.BytesIO]:
    info = tarfile.TarInfo(name=name)
    info.size = len(payload)
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info, io.BytesIO(payload)


def _archive_payload(
    captured: Sequence[CapturedFile], manifest_payload: bytes
) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT
    ) as archive:
        info, content = _tar_entry("manifest.json", manifest_payload)
        archive.addfile(info, content)
        archived_paths: set[str] = set()
        for item in sorted(captured, key=lambda record: record.archive_path):
            if item.archive_path in archived_paths:
                continue
            archived_paths.add(item.archive_path)
            info, content = _tar_entry(item.archive_path, item.payload)
            archive.addfile(info, content)
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as handle:
        handle.write(tar_buffer.getvalue())
    return compressed.getvalue()


def _verify_archive_payload(archive_payload: bytes, manifest_payload: bytes) -> None:
    try:
        manifest = json.loads(manifest_payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"generated manifest is invalid: {exc}") from exc
    expected = {"manifest.json": manifest_payload}
    expected_sizes = {"manifest.json": len(manifest_payload)}
    for record in manifest["files"]:
        expected.setdefault(record["archive_path"], None)
        prior_size = expected_sizes.setdefault(
            record["archive_path"], record["size_bytes"]
        )
        if prior_size != record["size_bytes"]:
            raise ValueError("generated manifest has conflicting member sizes")

    with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError(
                "generated archive has missing, duplicate or unexpected members"
            )
        records = {record["archive_path"]: record for record in manifest["files"]}
        for member in members:
            if not member.isfile() or member.mode != 0o644 or member.mtime != 0:
                raise ValueError(
                    f"generated archive member metadata is invalid: {member.name}"
                )
            if member.size != expected_sizes[member.name]:
                raise ValueError(
                    f"generated archive member header size is invalid: {member.name}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(
                    f"generated archive member is unreadable: {member.name}"
                )
            payload = extracted.read()
            if member.name == "manifest.json":
                if payload != manifest_payload:
                    raise ValueError(
                        "generated archive manifest does not match sidecar manifest"
                    )
                continue
            record = records[member.name]
            if (
                len(payload) != record["size_bytes"]
                or _sha256_bytes(payload) != record["sha256"]
            ):
                raise ValueError(
                    f"generated archive member digest mismatch: {member.name}"
                )


def _parse_root(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not ROOT_LABEL_PATTERN.fullmatch(label):
        raise ValueError(
            "include-root must use a unique safe label=/absolute/directory"
        )
    path = Path(raw_path).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"include-root is not an absolute directory: {raw_path}")
    return label, path.resolve()


def _allowed_roots(args: argparse.Namespace) -> list[AllowedRoot]:
    candidates = [
        ("campaign", args.authorization.parent.resolve()),
        ("authority", args.approval_policy.parent.resolve()),
        *(_parse_root(value) for value in args.include_root),
    ]
    labels: set[str] = set()
    paths: set[Path] = set()
    roots: list[AllowedRoot] = []
    for label, path in candidates:
        if label in labels:
            raise ValueError(f"duplicate allowed-root label: {label}")
        labels.add(label)
        if path in paths:
            continue
        paths.add(path)
        roots.append(AllowedRoot(label=label, path=path))
    return sorted(roots, key=lambda item: item.label)


def create_archive(
    args: argparse.Namespace,
) -> tuple[bytes, bytes, bytes, dict[str, Any]]:
    operation_time = datetime.now(timezone.utc)
    created_at = _parse_time(args.created_at) if args.created_at else operation_time
    if created_at > operation_time:
        raise ValueError("created-at cannot be in the future")
    if operation_time - created_at > timedelta(seconds=MAX_CREATED_AT_SKEW_SECONDS):
        raise ValueError("created-at cannot be more than 300 seconds before archiving")
    authorization = args.authorization.resolve()
    approval_policy = args.approval_policy.resolve()
    roots = _allowed_roots(args)
    supplemental_files = [
        _resolve_input_file(path, roots=roots) for path in args.supplemental_file
    ]
    first_result = _evaluate(
        authorization,
        approval_policy,
        now=operation_time,
        allow_legacy_unbound=args.allow_legacy_unbound,
    )
    canonical_result = _evaluate(
        authorization,
        approval_policy,
        now=created_at,
        allow_legacy_unbound=args.allow_legacy_unbound,
    )
    captured, captured_references = _capture_reference_closure(
        [authorization, approval_policy, *supplemental_files],
        authorization_path=authorization,
        roots=roots,
        max_file_bytes=args.max_file_bytes,
        max_total_bytes=args.max_total_bytes,
    )

    second_result = _evaluate(
        authorization,
        approval_policy,
        now=operation_time,
        allow_legacy_unbound=args.allow_legacy_unbound,
    )
    if second_result != first_result:
        raise ValueError("authorization evaluation changed during archive creation")
    for item in captured:
        if item.path.read_bytes() != item.payload:
            raise ValueError(
                f"referenced file changed during archive creation: {item.path}"
            )
    portable_paths: dict[str, Path] = {}
    for reference in captured_references:
        previous = portable_paths.setdefault(reference.raw_path, reference.target)
        if previous != reference.target:
            raise ValueError(
                f"one raw evidence path resolves to multiple files: {reference.raw_path}"
            )
    with ga_file_resolution_overrides(portable_paths, strict=True):
        portable_result = _evaluate(
            authorization,
            approval_policy,
            now=created_at,
            allow_legacy_unbound=args.allow_legacy_unbound,
        )
    if portable_result != canonical_result:
        raise ValueError("captured reference closure is not independently GA_AUTHORIZED")

    manifest = _manifest(
        captured=captured,
        captured_references=captured_references,
        authorization=authorization,
        approval_policy=approval_policy,
        supplemental_files=supplemental_files,
        result=portable_result,
        roots=roots,
        created_at=created_at,
    )
    manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    archive_payload = _archive_payload(captured, manifest_payload)
    _verify_archive_payload(archive_payload, manifest_payload)
    archive_digest = _sha256_bytes(archive_payload)
    digest_payload = f"{archive_digest}  {args.output.name}\n".encode("utf-8")
    summary = {
        "schema_version": ARCHIVE_MANIFEST_SCHEMA_VERSION,
        "status": "GA_AUTHORIZED_ARCHIVED",
        "release_digest": portable_result["release_digest"],
        "archive_sha256": archive_digest,
        "manifest_sha256": _sha256_bytes(manifest_payload),
        "file_count": len(captured),
    }
    return archive_payload, manifest_payload, digest_payload, summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument(
        "--include-root",
        action="append",
        default=[],
        help="extra allowed evidence root as label=/absolute/directory; repeatable",
    )
    parser.add_argument(
        "--supplemental-file",
        type=Path,
        action="append",
        default=[],
        help="explicitly archive a non-authoritative receipt/result; repeatable",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--digest-output", type=Path, required=True)
    parser.add_argument(
        "--allow-legacy-unbound",
        action="store_true",
        help="archive historical v1 authorizations only; never use for a formal GA release",
    )
    parser.add_argument("--created-at", help="RFC3339 deterministic manifest time")
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.authorization, "authorization"),
            (args.approval_policy, "approval policy"),
        ):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} must be a regular non-symlink file: {path}")
        outputs = [
            args.output.resolve(),
            args.manifest_output.resolve(),
            args.digest_output.resolve(),
        ]
        if len(set(outputs)) != 3:
            raise ValueError("archive, manifest and digest outputs must be distinct")
        if "\n" in args.output.name or "\r" in args.output.name:
            raise ValueError("archive output filename must not contain a newline")
        if any(path.exists() for path in outputs):
            raise ValueError(
                "an output already exists; authorized archives are immutable"
            )
        inputs = {args.authorization.resolve(), args.approval_policy.resolve()}
        for path in args.supplemental_file:
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    f"supplemental input must be a regular non-symlink file: {path}"
                )
            inputs.add(path.resolve())
        if len(inputs) != len(args.supplemental_file) + 2:
            raise ValueError(
                "authorization, policy and supplemental inputs must be distinct"
            )
        if any(path in inputs for path in outputs):
            raise ValueError("an archive output must not overwrite an input")
        if args.max_file_bytes <= 0 or args.max_total_bytes <= 0:
            raise ValueError("archive byte limits must be positive")
        if args.max_total_bytes < args.max_file_bytes:
            raise ValueError("max-total-bytes must be at least max-file-bytes")
        _allowed_roots(args)
        if args.created_at:
            _parse_time(args.created_at)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    written: list[tuple[Path, bytes]] = []
    try:
        archive_payload, manifest_payload, digest_payload, summary = create_archive(
            args
        )
        for path, payload in (
            (args.output, archive_payload),
            (args.manifest_output, manifest_payload),
            (args.digest_output, digest_payload),
        ):
            _atomic_write(path, payload)
            written.append((path, payload))
        if (
            args.output.read_bytes() != archive_payload
            or args.manifest_output.read_bytes() != manifest_payload
            or args.digest_output.read_bytes() != digest_payload
        ):
            raise ValueError("persisted archive outputs changed after immutable write")
        _verify_archive_payload(
            args.output.read_bytes(),
            args.manifest_output.read_bytes(),
        )
    except (OSError, UnicodeError, ValueError, tarfile.TarError) as exc:
        for path, payload in written:
            try:
                if (
                    not path.is_symlink()
                    and path.is_file()
                    and path.read_bytes() == payload
                ):
                    path.unlink()
            except OSError:
                pass
        print(f"GA authorized archive failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
