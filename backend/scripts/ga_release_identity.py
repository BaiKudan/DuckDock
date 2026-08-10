"""Shared immutable release binding for target-environment GA evidence."""

from __future__ import annotations

import re
from typing import Any


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
EVIDENCE_SCOPES = {"target-production", "local-validation"}
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")


def build_release_binding(
    *,
    scope: str,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
) -> dict[str, Any]:
    if scope not in EVIDENCE_SCOPES:
        raise ValueError(f"scope must be one of: {', '.join(sorted(EVIDENCE_SCOPES))}")
    if not target_environment.strip() or any(marker in target_environment for marker in PLACEHOLDER_MARKERS):
        raise ValueError("target environment must be a non-placeholder identifier")
    if not COMMIT_RE.fullmatch(source_commit):
        raise ValueError("source commit must be a lowercase 40-character Git SHA")
    if not IMAGE_RE.fullmatch(backend_image):
        raise ValueError("backend image must use registry/repository@sha256:<64 lowercase hex>")
    if not IMAGE_RE.fullmatch(frontend_image):
        raise ValueError("frontend image must use registry/repository@sha256:<64 lowercase hex>")
    return {
        "scope": scope,
        "target_environment": target_environment,
        "source_commit": source_commit,
        "images": {
            "backend": {"name": backend_image},
            "frontend": {"name": frontend_image},
        },
    }
