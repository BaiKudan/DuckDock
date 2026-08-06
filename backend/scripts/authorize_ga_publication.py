#!/usr/bin/env python3
"""Authorize publication of an exact DuckDock 2.0 GA archive and draft release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

try:
    from scripts.ga_release_identity import COMMIT_RE, IMAGE_RE, PLACEHOLDER_MARKERS
    from scripts.verify_ga_authorized_archive import (
        DEFAULT_MAX_ARCHIVE_BYTES,
        DEFAULT_MAX_MANIFEST_BYTES,
        DEFAULT_MAX_TOTAL_BYTES,
        verify_with_context,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_identity import COMMIT_RE, IMAGE_RE, PLACEHOLDER_MARKERS
    from verify_ga_authorized_archive import (
        DEFAULT_MAX_ARCHIVE_BYTES,
        DEFAULT_MAX_MANIFEST_BYTES,
        DEFAULT_MAX_TOTAL_BYTES,
        verify_with_context,
    )


PUBLICATION_SCHEMA_VERSION = "duckdock-ga-publication-authorization-v1"
PUBLICATION_STATUS = "GA_PUBLICATION_AUTHORIZED"
FORMAL_VERSION = "2.0.0"
FORMAL_TAG = "v2.0.0"
DEFAULT_MAX_AUTHORIZATION_AGE_HOURS = 24
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.@+-]{1,100}$")


def _parse_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be RFC3339: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _meaningful(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(marker in value for marker in PLACEHOLDER_MARKERS)
    )


def _https_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an HTTPS URL without embedded credentials")
    return value


def _atomic_create(path: Path, payload: bytes) -> None:
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


def authorize(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    verifier_args = argparse.Namespace(
        archive=args.archive,
        manifest=args.manifest,
        digest=args.digest,
        expected_sha256=args.expected_sha256,
        max_archive_bytes=args.max_archive_bytes,
        max_manifest_bytes=args.max_manifest_bytes,
        max_total_bytes=args.max_total_bytes,
        allow_legacy_unbound=False,
    )
    verification, context = verify_with_context(verifier_args)
    if verification.get("status") != "GA_AUTHORIZED_ARCHIVE_VERIFIED":
        raise ValueError("authorized archive did not pass independent verification")

    release = context["release"]
    target = context["target"]
    release_build = context["release_build"]
    if not (
        release.get("version") == FORMAL_VERSION
        and release.get("git_commit") == args.expected_git_commit
        and COMMIT_RE.fullmatch(str(release.get("git_commit", "")))
        and IMAGE_RE.fullmatch(str(release.get("backend_image", "")))
        and IMAGE_RE.fullmatch(str(release.get("frontend_image", "")))
        and SHA256_RE.fullmatch(str(release.get("contract_digest", "")))
    ):
        raise ValueError("archive release coordinates do not match the formal 2.0.0 publication")
    if not (
        _meaningful(target.get("target_id"))
        and target.get("environment") == "production"
        and target.get("deployment_mode") == "kubernetes-ha"
        and isinstance(target.get("public_base_url"), str)
    ):
        raise ValueError("archive target is not a named production kubernetes-ha deployment")
    _https_url(str(target["public_base_url"]), "authorized target public_base_url")

    checked_at = _parse_time(str(verification["checked_at"]), "archive checked_at")
    age = issued_at - checked_at
    if age < timedelta(seconds=-300):
        raise ValueError("publication time precedes the canonical archive authorization")
    if age > timedelta(hours=DEFAULT_MAX_AUTHORIZATION_AGE_HOURS):
        raise ValueError("GA authorization is too old for formal publication; rerun the complete gate")

    source_repository = _https_url(args.source_repository, "source repository")
    expected_source_repository = f"https://github.com/{args.workflow_repository}"
    expected_workflow_ref = (
        f"{args.workflow_repository}/.github/workflows/ci.yml@refs/tags/{FORMAL_TAG}"
    )
    if not (
        source_repository.rstrip("/") == expected_source_repository
        and release_build.get("source_repository") == expected_source_repository
        and release_build.get("workflow_ref") == expected_workflow_ref
        and isinstance(release_build.get("workflow_run_id"), str)
        and str(release_build["workflow_run_id"]).isdigit()
        and release_build.get("workflow_run_url")
        == f"https://github.com/{args.workflow_repository}/actions/runs/"
        f"{release_build['workflow_run_id']}"
    ):
        raise ValueError("source repository does not match the protected workflow repository")
    expected_run_url = (
        f"https://github.com/{args.workflow_repository}/actions/runs/{args.workflow_run_id}"
    )
    if _https_url(args.workflow_run_url, "workflow run URL").rstrip("/") != expected_run_url:
        raise ValueError("workflow run URL does not match repository and run ID")
    release_url = _https_url(args.draft_release_url, "draft release URL")
    if not release_url.startswith(f"https://github.com/{args.workflow_repository}/releases/"):
        raise ValueError("draft release URL does not belong to the workflow repository")

    archive_digest = _sha256(args.archive)
    manifest_digest = _sha256(args.manifest)
    if (
        archive_digest != verification["archive_sha256"]
        or manifest_digest != verification["manifest_sha256"]
    ):
        raise ValueError("archive or manifest changed after independent verification")

    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "status": PUBLICATION_STATUS,
        "issued_at": issued_at.isoformat(),
        "next_action": "publish_prevalidated_draft_release",
        "does_not_prove_external_publication": True,
        "release": {
            "version": FORMAL_VERSION,
            "git_tag": FORMAL_TAG,
            "git_commit": release["git_commit"],
            "backend_image": release["backend_image"],
            "frontend_image": release["frontend_image"],
            "contract_digest": release["contract_digest"],
        },
        "target": target,
        "release_build": release_build,
        "authorized_archive": {
            "archive_name": args.archive.name,
            "archive_sha256": archive_digest,
            "manifest_name": args.manifest.name,
            "manifest_sha256": manifest_digest,
            "release_digest": verification["release_digest"],
            "authorization_checked_at": verification["checked_at"],
            "file_count": verification["file_count"],
            "reference_count": verification["reference_count"],
        },
        "publication": {
            "source_repository": source_repository,
            "draft_release_id": args.draft_release_id,
            "draft_release_url": release_url,
            "workflow_repository": args.workflow_repository,
            "workflow_run_id": args.workflow_run_id,
            "workflow_run_url": args.workflow_run_url,
            "actor": args.actor,
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    digest_group = parser.add_mutually_exclusive_group(required=True)
    digest_group.add_argument("--digest", type=Path)
    digest_group.add_argument("--expected-sha256")
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--workflow-repository", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-url", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--draft-release-id", required=True)
    parser.add_argument("--draft-release-url", required=True)
    parser.add_argument("--issued-at")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--digest-output", type=Path, required=True)
    parser.add_argument("--max-archive-bytes", type=int, default=DEFAULT_MAX_ARCHIVE_BYTES)
    parser.add_argument("--max-manifest-bytes", type=int, default=DEFAULT_MAX_MANIFEST_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    args = parser.parse_args(argv)
    try:
        for path, label in ((args.archive, "archive"), (args.manifest, "manifest")):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} must be a regular non-symlink file: {path}")
        if args.digest is not None and (args.digest.is_symlink() or not args.digest.is_file()):
            raise ValueError(f"digest must be a regular non-symlink file: {args.digest}")
        if not COMMIT_RE.fullmatch(args.expected_git_commit):
            raise ValueError("expected git commit must be a lowercase 40-character SHA")
        if not REPOSITORY_RE.fullmatch(args.workflow_repository):
            raise ValueError("workflow repository must be owner/name")
        for value, label in (
            (args.workflow_run_id, "workflow run ID"),
            (args.draft_release_id, "draft release ID"),
        ):
            if not value.isdigit():
                raise ValueError(f"{label} must be numeric")
        if not IDENTIFIER_RE.fullmatch(args.actor):
            raise ValueError("actor is not a safe exact identity")
        if any(limit <= 0 for limit in (args.max_archive_bytes, args.max_manifest_bytes, args.max_total_bytes)):
            raise ValueError("verifier byte limits must be positive")
        if args.output.resolve() == args.digest_output.resolve():
            raise ValueError("receipt and digest outputs must be distinct")
        if args.output.exists() or args.digest_output.exists():
            raise ValueError("an output already exists; publication authorization is immutable")
        if args.issued_at is not None:
            _parse_time(args.issued_at, "issued-at")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    current = _parse_time(args.issued_at, "issued-at") if args.issued_at else None
    created: list[Path] = []
    try:
        receipt = authorize(args, now=current)
        payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_create(args.output, payload)
        created.append(args.output)
        receipt_digest = hashlib.sha256(payload).hexdigest()
        digest_payload = f"{receipt_digest}  {args.output.name}\n".encode("utf-8")
        _atomic_create(args.digest_output, digest_payload)
        created.append(args.digest_output)
        if (
            _sha256(args.output) != receipt_digest
            or args.digest_output.read_bytes() != digest_payload
            or _sha256(args.archive) != receipt["authorized_archive"]["archive_sha256"]
            or _sha256(args.manifest) != receipt["authorized_archive"]["manifest_sha256"]
        ):
            raise ValueError("persisted publication authorization did not re-verify")
    except (OSError, UnicodeError, ValueError, tarfile.TarError) as exc:
        for path in created:
            path.unlink(missing_ok=True)
        print(f"GA publication authorization failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
