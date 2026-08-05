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
    from app.models.deployment import AgentDeployment
    from app.models.package_registry import AgentPackageVersion
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseEnvironmentKind(str, enum.Enum):
    DEVELOPMENT = "DEVELOPMENT"
    TEST = "TEST"
    STAGING = "STAGING"
    CANARY = "CANARY"
    PRODUCTION = "PRODUCTION"


class ReleaseEnvironmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ReleasePolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ReleasePolicyMode(str, enum.Enum):
    SHADOW = "SHADOW"
    WARN = "WARN"
    ENFORCE = "ENFORCE"


class ReleasePolicyRuleType(str, enum.Enum):
    PACKAGE_EVIDENCE_VERIFIED = "PACKAGE_EVIDENCE_VERIFIED"
    SIGNING_KEY_ACTIVE = "SIGNING_KEY_ACTIVE"
    EVALUATION_GATE_PASS = "EVALUATION_GATE_PASS"
    RISK_TIER_ALLOWED = "RISK_TIER_ALLOWED"
    MAX_TOOL_ADDITIONS = "MAX_TOOL_ADDITIONS"
    FORBID_CAPABILITY_EXPANSION = "FORBID_CAPABILITY_EXPANSION"
    MAX_VULNERABILITY_SEVERITY = "MAX_VULNERABILITY_SEVERITY"
    ROLLBACK_TARGET_REQUIRED = "ROLLBACK_TARGET_REQUIRED"


class ReleasePolicyRawOutcome(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ReleasePolicyEnforcementOutcome(str, enum.Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"


class ReleasePolicyRuleVerdict(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReleaseApprovalDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReleaseApprovalRole(str, enum.Enum):
    OWNER = "OWNER"
    REVIEWER = "REVIEWER"
    SECURITY = "SECURITY"
    OPERATIONS = "OPERATIONS"
    RELEASE_MANAGER = "RELEASE_MANAGER"


class ReleaseExceptionReviewDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReleasePromotionStrategy(str, enum.Enum):
    ALL_AT_ONCE = "ALL_AT_ONCE"
    CANARY = "CANARY"
    ROLLING = "ROLLING"


class ReleasePromotionStatus(str, enum.Enum):
    DISPATCHED = "DISPATCHED"
    OBSERVING = "OBSERVING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLBACK_REQUESTED = "ROLLBACK_REQUESTED"
    ROLLED_BACK = "ROLLED_BACK"


class ReleaseReceiptKind(str, enum.Enum):
    PROMOTION = "PROMOTION"
    ROLLBACK = "ROLLBACK"


class ReleaseReceiptStatus(str, enum.Enum):
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    MISMATCH = "MISMATCH"


class ReleaseEnvironmentReleaseStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"


class ReleaseCanaryOutcome(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class ReleaseRollbackStatus(str, enum.Enum):
    DISPATCHED = "DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReleaseEnvironment(Base):
    """A Namespace-owned release stage in an explicit promotion graph."""

    __tablename__ = "release_environments"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_environments_public_id"),
        UniqueConstraint("namespace_id", "name", name="uq_release_environments_ns_name"),
        UniqueConstraint(
            "namespace_id",
            "promotion_order",
            name="uq_release_environments_ns_order",
        ),
        CheckConstraint("promotion_order >= 0", name="ck_release_environments_nonnegative_order"),
        CheckConstraint("minimum_approvals >= 0", name="ck_release_environments_nonnegative_approvals"),
        CheckConstraint(
            "protected = 1 OR minimum_approvals = 0",
            name="ck_release_environments_unprotected_no_approvals",
        ),
        Index(
            "ix_release_environments_ns_status_order",
            "namespace_id",
            "status",
            "promotion_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[ReleaseEnvironmentKind] = mapped_column(
        Enum(ReleaseEnvironmentKind, name="release_environment_kind"), nullable=False, index=True
    )
    promotion_order: Mapped[int] = mapped_column(Integer, nullable=False)
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minimum_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requires_canary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[ReleaseEnvironmentStatus] = mapped_column(
        Enum(ReleaseEnvironmentStatus, name="release_environment_status"),
        nullable=False,
        default=ReleaseEnvironmentStatus.ACTIVE,
        index=True,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    created_by_user: Mapped["User"] = relationship()


class ReleasePolicy(Base):
    """Stable Namespace identity for append-only release policy versions."""

    __tablename__ = "release_policies"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_policies_public_id"),
        UniqueConstraint("namespace_id", "name", name="uq_release_policies_ns_name"),
        Index("ix_release_policies_ns_status_created", "namespace_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[ReleasePolicyStatus] = mapped_column(
        Enum(ReleasePolicyStatus, name="release_policy_status"),
        nullable=False,
        default=ReleasePolicyStatus.ACTIVE,
        index=True,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    created_by_user: Mapped["User"] = relationship()
    versions: Mapped[list["ReleasePolicyVersion"]] = relationship(
        back_populates="policy", order_by="ReleasePolicyVersion.version", lazy="selectin"
    )


class ReleasePolicyVersion(Base):
    """Immutable, canonical release policy version."""

    __tablename__ = "release_policy_versions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_policy_versions_public_id"),
        UniqueConstraint("policy_id", "version", name="uq_release_policy_versions_policy_version"),
        UniqueConstraint(
            "policy_id",
            "content_digest",
            name="uq_release_policy_versions_policy_digest",
        ),
        CheckConstraint("version >= 1", name="ck_release_policy_versions_positive_version"),
        CheckConstraint("rule_count >= 1 AND rule_count <= 100", name="ck_release_policy_versions_rule_count"),
        Index("ix_release_policy_versions_target_mode", "target_environment_id", "mode"),
        Index("ix_release_policy_versions_content_digest", "content_digest"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("release_policies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_environment_id: Mapped[int] = mapped_column(
        ForeignKey("release_environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    mode: Mapped[ReleasePolicyMode] = mapped_column(
        Enum(ReleasePolicyMode, name="release_policy_mode"), nullable=False, index=True
    )
    rules_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    rule_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy: Mapped[ReleasePolicy] = relationship(back_populates="versions")
    target_environment: Mapped[ReleaseEnvironment] = relationship(lazy="joined")
    created_by_user: Mapped["User"] = relationship()


class ReleaseCandidate(Base):
    """Immutable candidate joining package, deployment and target environment."""

    __tablename__ = "release_candidates"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_candidates_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidates_ns_idempotency",
        ),
        UniqueConstraint(
            "namespace_id",
            "candidate_digest",
            name="uq_release_candidates_ns_digest",
        ),
        UniqueConstraint(
            "package_version_id",
            "deployment_id",
            "target_environment_id",
            "baseline_candidate_id",
            name="uq_release_candidates_exact_subject",
        ),
        Index("ix_release_candidates_ns_created", "namespace_id", "created_at"),
        Index("ix_release_candidates_target_created", "target_environment_id", "created_at"),
        Index("ix_release_candidates_digest", "candidate_digest"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("agent_package_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_environment_id: Mapped[int] = mapped_column(
        ForeignKey("release_environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    baseline_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    package_version: Mapped["AgentPackageVersion"] = relationship(lazy="selectin")
    deployment: Mapped["AgentDeployment"] = relationship(lazy="selectin")
    target_environment: Mapped[ReleaseEnvironment] = relationship(lazy="joined")
    baseline_candidate: Mapped["ReleaseCandidate | None"] = relationship(
        remote_side="ReleaseCandidate.id", lazy="joined"
    )
    created_by_user: Mapped["User"] = relationship()


class ReleasePolicyDecision(Base):
    """Immutable evaluation of one exact candidate under one policy version."""

    __tablename__ = "release_policy_decisions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_policy_decisions_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_policy_decisions_ns_idempotency",
        ),
        UniqueConstraint(
            "candidate_id",
            "policy_version_id",
            "evidence_snapshot_digest",
            name="uq_release_policy_decisions_exact_evidence",
        ),
        CheckConstraint("evaluation_duration_ms >= 0", name="ck_release_policy_decisions_duration"),
        Index("ix_release_policy_decisions_candidate_created", "candidate_id", "created_at"),
        Index("ix_release_policy_decisions_ns_outcome", "namespace_id", "enforcement_outcome"),
        Index("ix_release_policy_decisions_digest", "decision_digest"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey("release_policy_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_outcome: Mapped[ReleasePolicyRawOutcome] = mapped_column(
        Enum(ReleasePolicyRawOutcome, name="release_policy_raw_outcome"), nullable=False, index=True
    )
    enforcement_outcome: Mapped[ReleasePolicyEnforcementOutcome] = mapped_column(
        Enum(ReleasePolicyEnforcementOutcome, name="release_policy_enforcement_outcome"),
        nullable=False,
        index=True,
    )
    would_block: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    evaluated_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[ReleaseCandidate] = relationship(lazy="selectin")
    policy_version: Mapped[ReleasePolicyVersion] = relationship(lazy="selectin")
    evaluated_by_user: Mapped["User"] = relationship()
    rule_results: Mapped[list["ReleasePolicyRuleResult"]] = relationship(
        back_populates="decision",
        cascade="all, delete-orphan",
        order_by="ReleasePolicyRuleResult.position",
        lazy="selectin",
    )


class ReleasePolicyRuleResult(Base):
    """Bounded explainability row for one policy rule."""

    __tablename__ = "release_policy_rule_results"
    __table_args__ = (
        UniqueConstraint("decision_id", "rule_id", name="uq_release_policy_rule_results_rule"),
        UniqueConstraint("decision_id", "position", name="uq_release_policy_rule_results_position"),
        CheckConstraint("position >= 0 AND position < 100", name="ck_release_policy_rule_results_position"),
        Index("ix_release_policy_rule_results_type_verdict", "rule_type", "verdict"),
        Index("ix_release_policy_rule_results_evidence_digest", "evidence_digest"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("release_policy_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_type: Mapped[ReleasePolicyRuleType] = mapped_column(
        Enum(ReleasePolicyRuleType, name="release_policy_rule_type"), nullable=False, index=True
    )
    verdict: Mapped[ReleasePolicyRuleVerdict] = mapped_column(
        Enum(ReleasePolicyRuleVerdict, name="release_policy_rule_verdict"), nullable=False, index=True
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    decision: Mapped[ReleasePolicyDecision] = relationship(back_populates="rule_results")


class ReleaseCandidateApproval(Base):
    """One immutable human decision for an exact candidate and policy decision."""

    __tablename__ = "release_candidate_approvals"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_candidate_approvals_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidate_approvals_ns_idempotency",
        ),
        UniqueConstraint(
            "candidate_id",
            "policy_decision_id",
            "reviewed_by_user_id",
            name="uq_release_candidate_approvals_reviewer",
        ),
        CheckConstraint(
            "decision = 'APPROVED' OR (comment IS NOT NULL AND LENGTH(comment) >= 5)",
            name="ck_release_candidate_approvals_rejection_comment",
        ),
        Index("ix_release_candidate_approvals_candidate_created", "candidate_id", "created_at"),
        Index(
            "ix_release_candidate_approvals_policy_decision",
            "policy_decision_id",
            "decision",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_decision_id: Mapped[int] = mapped_column(
        ForeignKey("release_policy_decisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision: Mapped[ReleaseApprovalDecision] = mapped_column(
        Enum(ReleaseApprovalDecision, name="release_approval_decision"), nullable=False, index=True
    )
    role: Mapped[ReleaseApprovalRole] = mapped_column(
        Enum(ReleaseApprovalRole, name="release_approval_role"), nullable=False, index=True
    )
    comment: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[ReleaseCandidate] = relationship(lazy="selectin")
    policy_decision: Mapped[ReleasePolicyDecision] = relationship(lazy="selectin")
    reviewed_by_user: Mapped["User"] = relationship()


class ReleasePolicyException(Base):
    """Time-bounded request to waive exact failed rules on one decision."""

    __tablename__ = "release_policy_exceptions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_policy_exceptions_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_policy_exceptions_ns_idempotency",
        ),
        CheckConstraint("expires_at > created_at", name="ck_release_policy_exceptions_future_expiry"),
        CheckConstraint(
            "waived_rule_count >= 1 AND waived_rule_count <= 100",
            name="ck_release_policy_exceptions_rule_count",
        ),
        Index("ix_release_policy_exceptions_decision_expiry", "policy_decision_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_decision_id: Mapped[int] = mapped_column(
        ForeignKey("release_policy_decisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    waived_rule_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    waived_rule_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    exception_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[ReleaseCandidate] = relationship(lazy="selectin")
    policy_decision: Mapped[ReleasePolicyDecision] = relationship(lazy="selectin")
    requested_by_user: Mapped["User"] = relationship()
    review: Mapped["ReleasePolicyExceptionReview | None"] = relationship(
        back_populates="exception", uselist=False, lazy="selectin"
    )


class ReleasePolicyExceptionReview(Base):
    """Independent immutable review of a bounded Policy exception."""

    __tablename__ = "release_policy_exception_reviews"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_policy_exception_reviews_public_id"),
        UniqueConstraint("exception_id", name="uq_release_policy_exception_reviews_exception"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_policy_exception_reviews_ns_idempotency",
        ),
        CheckConstraint(
            "decision = 'APPROVED' OR (comment IS NOT NULL AND LENGTH(comment) >= 5)",
            name="ck_release_policy_exception_reviews_rejection_comment",
        ),
        Index(
            "ix_release_policy_exception_reviews_decision_created",
            "decision",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    exception_id: Mapped[int] = mapped_column(
        ForeignKey("release_policy_exceptions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision: Mapped[ReleaseExceptionReviewDecision] = mapped_column(
        Enum(ReleaseExceptionReviewDecision, name="release_exception_review_decision"),
        nullable=False,
        index=True,
    )
    comment: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    exception: Mapped[ReleasePolicyException] = relationship(back_populates="review")
    reviewed_by_user: Mapped["User"] = relationship()


class ReleasePromotion(Base):
    """A dispatch request; success requires an authenticated Runtime receipt."""

    __tablename__ = "release_promotions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_promotions_public_id"),
        UniqueConstraint("dispatch_public_id", name="uq_release_promotions_dispatch_public_id"),
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_release_promotions_ns_idempotency"
        ),
        CheckConstraint(
            "(strategy = 'CANARY' AND canary_config_json IS NOT NULL) OR "
            "(strategy <> 'CANARY' AND canary_config_json IS NULL)",
            name="ck_release_promotions_canary_config",
        ),
        Index("ix_release_promotions_ns_status_created", "namespace_id", "status", "created_at"),
        Index("ix_release_promotions_candidate_created", "candidate_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    dispatch_public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_decision_id: Mapped[int] = mapped_column(
        ForeignKey("release_policy_decisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_environments.id", ondelete="RESTRICT"), index=True
    )
    target_environment_id: Mapped[int] = mapped_column(
        ForeignKey("release_environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    strategy: Mapped[ReleasePromotionStrategy] = mapped_column(
        Enum(ReleasePromotionStrategy, name="release_promotion_strategy"), nullable=False, index=True
    )
    status: Mapped[ReleasePromotionStatus] = mapped_column(
        Enum(ReleasePromotionStatus, name="release_promotion_status"),
        nullable=False,
        default=ReleasePromotionStatus.DISPATCHED,
        index=True,
    )
    acknowledge_warnings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canary_config_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    exception_digest: Mapped[str | None] = mapped_column(String(64))
    approval_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    dispatch_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    candidate: Mapped[ReleaseCandidate] = relationship(lazy="selectin")
    policy_decision: Mapped[ReleasePolicyDecision] = relationship(lazy="selectin")
    source_environment: Mapped[ReleaseEnvironment | None] = relationship(
        foreign_keys=[source_environment_id], lazy="joined"
    )
    target_environment: Mapped[ReleaseEnvironment] = relationship(
        foreign_keys=[target_environment_id], lazy="joined"
    )
    requested_by_user: Mapped["User"] = relationship()


class ReleaseRollback(Base):
    """An exact rollback dispatch targeting a previous successful release."""

    __tablename__ = "release_rollbacks"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_rollbacks_public_id"),
        UniqueConstraint("dispatch_public_id", name="uq_release_rollbacks_dispatch_public_id"),
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_release_rollbacks_ns_idempotency"
        ),
        Index("ix_release_rollbacks_promotion_status", "promotion_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    dispatch_public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    promotion_id: Mapped[int] = mapped_column(
        ForeignKey("release_promotions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    environment_id: Mapped[int] = mapped_column(
        ForeignKey("release_environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_environment_release_id: Mapped[int] = mapped_column(
        ForeignKey(
            "release_environment_releases.id",
            name="fk_release_rollbacks_target_release",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[ReleaseRollbackStatus] = mapped_column(
        Enum(ReleaseRollbackStatus, name="release_rollback_status"),
        nullable=False,
        default=ReleaseRollbackStatus.DISPATCHED,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    dispatch_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    promotion: Mapped[ReleasePromotion] = relationship(lazy="selectin")
    environment: Mapped[ReleaseEnvironment] = relationship(lazy="joined")
    source_candidate: Mapped[ReleaseCandidate] = relationship(lazy="selectin")
    target_environment_release: Mapped["ReleaseEnvironmentRelease"] = relationship(
        foreign_keys=[target_environment_release_id], lazy="selectin"
    )
    requested_by_user: Mapped["User"] = relationship()


class ReleaseDeploymentReceipt(Base):
    """Runtime-authenticated report of an actual promotion or rollback result."""

    __tablename__ = "release_deployment_receipts"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_deployment_receipts_public_id"),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "idempotency_key",
            name="uq_release_deployment_receipts_runtime_idempotency",
        ),
        UniqueConstraint(
            "runtime_id",
            "external_receipt_id",
            name="uq_release_deployment_receipts_runtime_external",
        ),
        CheckConstraint(
            "(kind = 'PROMOTION' AND promotion_id IS NOT NULL AND rollback_id IS NULL) OR "
            "(kind = 'ROLLBACK' AND promotion_id IS NULL AND rollback_id IS NOT NULL)",
            name="ck_release_deployment_receipts_dispatch_kind",
        ),
        CheckConstraint(
            "status <> 'APPLIED' OR error_code IS NULL",
            name="ck_release_deployment_receipts_applied_no_error",
        ),
        Index("ix_release_deployment_receipts_promotion_created", "promotion_id", "created_at"),
        Index("ix_release_deployment_receipts_rollback_created", "rollback_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_instances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reporter_credential_id: Mapped[int] = mapped_column(
        ForeignKey("reporter_credentials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[ReleaseReceiptKind] = mapped_column(
        Enum(ReleaseReceiptKind, name="release_receipt_kind"), nullable=False, index=True
    )
    promotion_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_promotions.id", ondelete="RESTRICT"), index=True
    )
    rollback_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_rollbacks.id", ondelete="RESTRICT"), index=True
    )
    external_receipt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ReleaseReceiptStatus] = mapped_column(
        Enum(ReleaseReceiptStatus, name="release_receipt_status"), nullable=False, index=True
    )
    observed_package_version_public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_deployment_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_configuration_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_release_ref: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    promotion: Mapped[ReleasePromotion | None] = relationship(lazy="selectin")
    rollback: Mapped[ReleaseRollback | None] = relationship(lazy="selectin")


class ReleaseEnvironmentRelease(Base):
    """Append-only activation history for an Environment."""

    __tablename__ = "release_environment_releases"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_environment_releases_public_id"),
        UniqueConstraint("receipt_id", name="uq_release_environment_releases_receipt"),
        CheckConstraint(
            "(promotion_id IS NOT NULL AND rollback_id IS NULL) OR "
            "(promotion_id IS NULL AND rollback_id IS NOT NULL)",
            name="ck_release_environment_releases_activation_source",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND deactivated_at IS NULL) OR "
            "(status IN ('SUPERSEDED', 'ROLLED_BACK') AND deactivated_at IS NOT NULL)",
            name="ck_release_environment_releases_status_time",
        ),
        Index("ix_release_environment_releases_environment_status", "environment_id", "status"),
        Index("ix_release_environment_releases_candidate_activated", "candidate_id", "activated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    environment_id: Mapped[int] = mapped_column(
        ForeignKey("release_environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    promotion_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_promotions.id", ondelete="RESTRICT"), index=True
    )
    rollback_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_rollbacks.id", ondelete="RESTRICT"), index=True
    )
    receipt_id: Mapped[int] = mapped_column(
        ForeignKey("release_deployment_receipts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    previous_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_environment_releases.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[ReleaseEnvironmentReleaseStatus] = mapped_column(
        Enum(ReleaseEnvironmentReleaseStatus, name="release_environment_release_status"),
        nullable=False,
        default=ReleaseEnvironmentReleaseStatus.ACTIVE,
        index=True,
    )
    activation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    environment: Mapped[ReleaseEnvironment] = relationship(lazy="joined")
    candidate: Mapped[ReleaseCandidate] = relationship(lazy="selectin")
    promotion: Mapped[ReleasePromotion | None] = relationship(lazy="selectin")
    rollback: Mapped[ReleaseRollback | None] = relationship(
        foreign_keys=[rollback_id], lazy="selectin"
    )
    receipt: Mapped[ReleaseDeploymentReceipt] = relationship(lazy="selectin")
    previous_release: Mapped["ReleaseEnvironmentRelease | None"] = relationship(
        remote_side="ReleaseEnvironmentRelease.id", lazy="joined"
    )


class ReleaseCanaryEvaluation(Base):
    """Bounded metadata-only canary outcome over one exact Deployment."""

    __tablename__ = "release_canary_evaluations"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_release_canary_evaluations_public_id"),
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_release_canary_evaluations_ns_idempotency"
        ),
        UniqueConstraint(
            "promotion_id",
            "evidence_digest",
            name="uq_release_canary_evaluations_evidence",
        ),
        CheckConstraint("completed_run_count >= 0", name="ck_release_canary_evaluations_run_count"),
        CheckConstraint(
            "failure_rate >= 0 AND failure_rate <= 1 AND "
            "untrusted_rate >= 0 AND untrusted_rate <= 1",
            name="ck_release_canary_evaluations_rates",
        ),
        CheckConstraint("window_end > window_start", name="ck_release_canary_evaluations_window"),
        Index("ix_release_canary_evaluations_promotion_created", "promotion_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    promotion_id: Mapped[int] = mapped_column(
        ForeignKey("release_promotions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    outcome: Mapped[ReleaseCanaryOutcome] = mapped_column(
        Enum(ReleaseCanaryOutcome, name="release_canary_outcome"), nullable=False, index=True
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_run_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_run_count: Mapped[int] = mapped_column(Integer, nullable=False)
    untrusted_run_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_rate: Mapped[float] = mapped_column(nullable=False)
    untrusted_rate: Mapped[float] = mapped_column(nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluated_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    promotion: Mapped[ReleasePromotion] = relationship(lazy="selectin")
    evaluated_by_user: Mapped["User"] = relationship()
