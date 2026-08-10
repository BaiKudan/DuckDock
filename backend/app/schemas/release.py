from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.release import ReleaseCandidateReviewDecision

EXACT_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
_LATEST_ALIASES = frozenset({"latest", "newest", "current"})


class ReleaseCandidateSelectorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    release_candidate_ref: str = Field(
        min_length=1,
        max_length=255,
        pattern=EXACT_REF_PATTERN,
    )
    deployment_public_id: str = Field(
        min_length=1,
        max_length=36,
        pattern=EXACT_REF_PATTERN,
    )
    deployment_revision: str = Field(
        min_length=1,
        max_length=128,
        pattern=EXACT_REF_PATTERN,
    )

    @field_validator(
        "release_candidate_ref",
        "deployment_public_id",
        "deployment_revision",
    )
    @classmethod
    def reject_latest_alias(cls, value: str) -> str:
        if value.casefold() in _LATEST_ALIASES:
            raise ValueError("exact release selector must not use latest aliases")
        return value


class ReleaseCandidateEvaluationBindingCreate(
    ReleaseCandidateSelectorIn
):
    evaluation_comparison_public_id: str = Field(
        min_length=1,
        max_length=36,
        pattern=EXACT_REF_PATTERN,
    )


class ReleaseCandidateEvaluationReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    binding_public_id: str = Field(
        min_length=1,
        max_length=36,
        pattern=EXACT_REF_PATTERN,
    )
    decision: ReleaseCandidateReviewDecision
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def rejection_requires_comment(
        self,
    ) -> "ReleaseCandidateEvaluationReviewCreate":
        if (
            self.decision == ReleaseCandidateReviewDecision.REJECTED
            and (self.comment is None or len(self.comment) < 5)
        ):
            raise ValueError(
                "Rejected review requires a comment of at least 5 characters"
            )
        return self


class ReleaseCandidateEvaluationReviewOut(BaseModel):
    public_id: str
    namespace_id: int
    binding_public_id: str
    decision: ReleaseCandidateReviewDecision
    comment: str | None
    schema_name: str
    schema_version: str
    review_digest: str = Field(pattern=SHA256_PATTERN)
    reviewed_by_user_id: int
    created_at: datetime


class ReleaseCandidateEvaluationBindingOut(BaseModel):
    public_id: str
    namespace_id: int
    release_candidate_ref: str
    deployment_public_id: str
    deployment_revision: str
    deployment_configuration_digest: str = Field(pattern=SHA256_PATTERN)
    evaluation_comparison_public_id: str
    comparison_outcome: str
    comparison_reproducibility_digest: str = Field(pattern=SHA256_PATTERN)
    candidate_evaluation_public_id: str
    candidate_manifest_public_id: str
    schema_name: str
    schema_version: str
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    created_by_user_id: int
    created_at: datetime
    review: ReleaseCandidateEvaluationReviewOut | None = None


class ReleaseGateEvidenceOut(BaseModel):
    evidence_id: str
    evidence_kind: str
    verdict: str
    evidence_digest: str = Field(pattern=SHA256_PATTERN)
    observed_at: datetime


class ReleaseCandidateGateDecisionOut(BaseModel):
    schema_name: str
    schema_version: str
    namespace_id: int
    release_candidate_ref: str
    deployment_public_id: str
    deployment_revision: str
    outcome: str
    reason_codes: list[str]
    evidence_count: int
    evidence: list[ReleaseGateEvidenceOut]
    decision_digest: str = Field(pattern=SHA256_PATTERN)
