from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.handover import (
    HandoverAcceptanceDecision,
    HandoverObligationReceiptDecision,
    HandoverObligationSeverity,
    HandoverObligationType,
    HandoverReadinessOutcome,
)


class HandoverEvidenceSnapshotCreate(BaseModel):
    handover_case_id: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class HandoverObligationReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    decision: HandoverObligationReceiptDecision
    note: str
    evidence_ids: list[int]
    receipt_digest: str
    decided_by_user_id: int
    created_at: datetime


class HandoverObligationOut(BaseModel):
    public_id: str
    namespace_id: int
    handover_case_id: int
    snapshot_public_id: str
    obligation_key: str
    obligation_type: HandoverObligationType
    severity: HandoverObligationSeverity
    title: str
    requirement: dict
    requires_evidence: bool
    obligation_digest: str
    receipt: HandoverObligationReceiptOut | None
    created_at: datetime


class HandoverEvidenceSnapshotOut(BaseModel):
    public_id: str
    namespace_id: int
    handover_case_id: int
    sequence: int
    subject: dict
    nodes: list[dict]
    edges: list[dict]
    evidence_summary: dict
    readiness: list[dict]
    node_count: int
    edge_count: int
    check_count: int
    readiness_outcome: HandoverReadinessOutcome
    current_readiness_outcome: HandoverReadinessOutcome
    blocking_obligation_count: int
    open_blocking_obligation_count: int
    snapshot_digest: str
    obligations: list[HandoverObligationOut]
    created_by_user_id: int
    created_at: datetime


class HandoverObligationReceiptCreate(BaseModel):
    decision: HandoverObligationReceiptDecision
    note: str = Field(min_length=2, max_length=1000)
    evidence_ids: list[int] = Field(default_factory=list, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[int]) -> list[int]:
        if any(item < 1 for item in value):
            raise ValueError("evidence_ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("evidence_ids must be unique")
        return sorted(value)


class HandoverAcceptanceCreate(BaseModel):
    snapshot_public_id: str = Field(min_length=1, max_length=40)
    decision: HandoverAcceptanceDecision
    comment: str = Field(min_length=2, max_length=1000)
    acknowledges_failures: bool = False
    idempotency_key: str = Field(min_length=1, max_length=128)


class HandoverAcceptanceOut(BaseModel):
    public_id: str
    namespace_id: int
    handover_case_id: int
    snapshot_public_id: str
    decision: HandoverAcceptanceDecision
    acknowledges_failures: bool
    comment: str
    obligation_receipt_digests: list[str]
    acceptance_digest: str
    accepted_by_user_id: int
    created_at: datetime


class HandoverSigningPayloadOut(BaseModel):
    schema_name: str
    schema_version: str
    acceptance_public_id: str
    snapshot_public_id: str
    manifest_digest: str
    payload_base64: str


class HandoverSignedPackageCreate(BaseModel):
    acceptance_public_id: str = Field(min_length=1, max_length=40)
    signing_key_public_id: str = Field(min_length=1, max_length=40)
    signature: str = Field(min_length=1, max_length=512)
    idempotency_key: str = Field(min_length=1, max_length=128)


class HandoverSignedPackageOut(BaseModel):
    public_id: str
    namespace_id: int
    handover_case_id: int
    snapshot_public_id: str
    acceptance_public_id: str
    evidence_item_id: int
    evidence_object_uri: str | None
    signing_key_public_id: str
    signing_key_fingerprint: str
    manifest_digest: str
    archive_digest: str
    signature_algorithm: str
    signature: str
    signature_digest: str
    attestation_digest: str
    created_by_user_id: int
    created_at: datetime
