"""Provider-neutral, candidate-pinned Release evidence lookup contracts.

This module deliberately contains no provider SDK and no database query.  It
defines the exact-key seam that a later Release Candidate specification can
implement without falling back to a Namespace's latest evaluation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


_EXACT_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}")
_EVIDENCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}")
_EVIDENCE_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_VERDICTS = frozenset({"pass", "fail", "inconclusive"})
_LATEST_ALIASES = frozenset({"latest", "newest", "current"})


def _validate_exact_ref(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _EXACT_REF_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be an exact opaque reference")
    if value.casefold() in _LATEST_ALIASES:
        raise ValueError(f"{field} must not use a latest-value alias")


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceSelector:
    """The complete immutable subject key for Release evidence."""

    namespace_id: int
    release_candidate_ref: str
    deployment_public_id: str
    deployment_revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.namespace_id, int) or self.namespace_id <= 0:
            raise ValueError("namespace_id must be a positive integer")
        _validate_exact_ref(
            self.release_candidate_ref,
            field="release_candidate_ref",
        )
        _validate_exact_ref(
            self.deployment_public_id,
            field="deployment_public_id",
        )
        _validate_exact_ref(
            self.deployment_revision,
            field="deployment_revision",
        )


@dataclass(frozen=True, slots=True)
class CandidateEvidenceReference:
    """Low-sensitive immutable reference returned by an evidence provider."""

    selector: ReleaseEvidenceSelector
    evidence_id: str
    evidence_kind: str
    verdict: str
    artifact_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if _EVIDENCE_ID_PATTERN.fullmatch(self.evidence_id) is None:
            raise ValueError("evidence_id must be an opaque reference")
        if _EVIDENCE_KIND_PATTERN.fullmatch(self.evidence_kind) is None:
            raise ValueError("evidence_kind must be a lower_snake_case identifier")
        if self.verdict not in _VERDICTS:
            raise ValueError("verdict must be pass, fail or inconclusive")
        if _SHA256_PATTERN.fullmatch(self.artifact_sha256) is None:
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 digest")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")


class ReleaseEvidenceLookupPort(Protocol):
    async def lookup(
        self,
        selector: ReleaseEvidenceSelector,
    ) -> tuple[CandidateEvidenceReference, ...]: ...


class NullReleaseEvidenceLookupPort:
    """Fail-closed default used when no evaluation evidence provider is bound."""

    async def lookup(
        self,
        selector: ReleaseEvidenceSelector,
    ) -> tuple[CandidateEvidenceReference, ...]:
        return ()
