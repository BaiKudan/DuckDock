from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.control_plane import EvidenceItem, HandoverCase
    from app.models.package_registry import PackageSigningKey
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HandoverReadinessOutcome(str, enum.Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class HandoverObligationType(str, enum.Enum):
    RECEIVER_ACCESS = "RECEIVER_ACCESS"
    OWNER_OR_FALLBACK = "OWNER_OR_FALLBACK"
    PRODUCTION_VERSION = "PRODUCTION_VERSION"
    EVALUATION_BASELINE = "EVALUATION_BASELINE"
    RUNBOOK = "RUNBOOK"
    RISK_EVIDENCE = "RISK_EVIDENCE"
    FAILED_ACTION_ACKNOWLEDGEMENT = "FAILED_ACTION_ACKNOWLEDGEMENT"


class HandoverObligationSeverity(str, enum.Enum):
    BLOCKING = "BLOCKING"
    ADVISORY = "ADVISORY"


class HandoverObligationReceiptDecision(str, enum.Enum):
    FULFILLED = "FULFILLED"
    FAILED = "FAILED"
    WAIVED = "WAIVED"


class HandoverAcceptanceDecision(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class HandoverEvidenceSnapshot(Base):
    """Immutable, metadata-only point-in-time handover evidence graph."""

    __tablename__ = "handover_evidence_snapshots"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_handover_evidence_snapshots_public_id"),
        UniqueConstraint(
            "handover_case_id",
            "sequence",
            name="uq_handover_evidence_snapshots_case_sequence",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_evidence_snapshots_ns_idempotency",
        ),
        CheckConstraint("sequence >= 1", name="ck_handover_evidence_snapshots_positive_sequence"),
        CheckConstraint("node_count >= 1", name="ck_handover_evidence_snapshots_node_count"),
        CheckConstraint("edge_count >= 0", name="ck_handover_evidence_snapshots_edge_count"),
        CheckConstraint("check_count >= 1", name="ck_handover_evidence_snapshots_check_count"),
        Index(
            "ix_handover_evidence_snapshots_case_created",
            "handover_case_id",
            "created_at",
        ),
        Index(
            "ix_handover_evidence_snapshots_ns_outcome_created",
            "namespace_id",
            "readiness_outcome",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    handover_case_id: Mapped[int] = mapped_column(
        ForeignKey("handover_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    nodes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    edges_json: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    readiness_json: Mapped[list] = mapped_column(JSON, nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False)
    check_count: Mapped[int] = mapped_column(Integer, nullable=False)
    readiness_outcome: Mapped[HandoverReadinessOutcome] = mapped_column(
        Enum(HandoverReadinessOutcome, name="handover_readiness_outcome"),
        nullable=False,
        index=True,
    )
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    handover_case: Mapped["HandoverCase"] = relationship(lazy="joined")
    created_by_user: Mapped["User"] = relationship()
    obligations: Mapped[list["HandoverObligation"]] = relationship(
        back_populates="snapshot", order_by="HandoverObligation.id", lazy="selectin"
    )


class HandoverObligation(Base):
    """Immutable requirement emitted by one evidence snapshot."""

    __tablename__ = "handover_obligations"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_handover_obligations_public_id"),
        UniqueConstraint(
            "snapshot_id",
            "obligation_key",
            name="uq_handover_obligations_snapshot_key",
        ),
        Index("ix_handover_obligations_case_severity", "handover_case_id", "severity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    handover_case_id: Mapped[int] = mapped_column(
        ForeignKey("handover_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("handover_evidence_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    obligation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    obligation_type: Mapped[HandoverObligationType] = mapped_column(
        Enum(HandoverObligationType, name="handover_obligation_type"),
        nullable=False,
        index=True,
    )
    severity: Mapped[HandoverObligationSeverity] = mapped_column(
        Enum(HandoverObligationSeverity, name="handover_obligation_severity"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    requires_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    obligation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    snapshot: Mapped[HandoverEvidenceSnapshot] = relationship(back_populates="obligations")
    receipts: Mapped[list["HandoverObligationReceipt"]] = relationship(
        back_populates="obligation", order_by="HandoverObligationReceipt.id", lazy="selectin"
    )


class HandoverObligationReceipt(Base):
    """Append-only terminal response to one obligation."""

    __tablename__ = "handover_obligation_receipts"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_handover_obligation_receipts_public_id"),
        UniqueConstraint("obligation_id", name="uq_handover_obligation_receipts_obligation"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_obligation_receipts_ns_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    obligation_id: Mapped[int] = mapped_column(
        ForeignKey("handover_obligations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision: Mapped[HandoverObligationReceiptDecision] = mapped_column(
        Enum(HandoverObligationReceiptDecision, name="handover_obligation_receipt_decision"),
        nullable=False,
        index=True,
    )
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    evidence_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    obligation: Mapped[HandoverObligation] = relationship(back_populates="receipts")
    decided_by_user: Mapped["User"] = relationship()


class HandoverAcceptance(Base):
    """Immutable receiver decision over one exact evidence snapshot."""

    __tablename__ = "handover_acceptances"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_handover_acceptances_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_acceptances_ns_idempotency",
        ),
        Index("ix_handover_acceptances_snapshot_created", "snapshot_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    handover_case_id: Mapped[int] = mapped_column(
        ForeignKey("handover_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("handover_evidence_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision: Mapped[HandoverAcceptanceDecision] = mapped_column(
        Enum(HandoverAcceptanceDecision, name="handover_acceptance_decision"),
        nullable=False,
        index=True,
    )
    acknowledges_failures: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    comment: Mapped[str] = mapped_column(String(1000), nullable=False)
    obligation_receipt_digests_json: Mapped[list] = mapped_column(JSON, nullable=False)
    acceptance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    snapshot: Mapped[HandoverEvidenceSnapshot] = relationship(lazy="selectin")
    accepted_by_user: Mapped["User"] = relationship()


class HandoverSignedPackage(Base):
    """Detached signature and immutable storage receipt for one v2 handover package."""

    __tablename__ = "handover_signed_packages"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_handover_signed_packages_public_id"),
        UniqueConstraint("acceptance_id", name="uq_handover_signed_packages_acceptance"),
        UniqueConstraint("evidence_item_id", name="uq_handover_signed_packages_evidence"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_signed_packages_ns_idempotency",
        ),
        Index("ix_handover_signed_packages_case_created", "handover_case_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    handover_case_id: Mapped[int] = mapped_column(
        ForeignKey("handover_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("handover_evidence_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    acceptance_id: Mapped[int] = mapped_column(
        ForeignKey("handover_acceptances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    evidence_item_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    signing_key_id: Mapped[int] = mapped_column(
        ForeignKey("package_signing_keys.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    signature_value: Mapped[str] = mapped_column(String(512), nullable=False)
    signature_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attestation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    acceptance: Mapped[HandoverAcceptance] = relationship(lazy="selectin")
    evidence_item: Mapped["EvidenceItem"] = relationship(lazy="joined")
    signing_key: Mapped["PackageSigningKey"] = relationship(lazy="joined")
    created_by_user: Mapped["User"] = relationship()
