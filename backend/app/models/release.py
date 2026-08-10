from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
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
    from app.models.evaluation import EvaluationComparison
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseCandidateReviewDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReleaseCandidateEvaluationBinding(Base):
    """Immutable binding from an exact release candidate to evaluation evidence."""

    __tablename__ = "release_candidate_evaluation_bindings"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_release_candidate_evaluation_bindings_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidate_evaluation_bindings_idempotency",
        ),
        UniqueConstraint(
            "namespace_id",
            "release_candidate_ref",
            "deployment_id",
            "evaluation_comparison_id",
            name="uq_release_candidate_evaluation_bindings_exact_pins",
        ),
        CheckConstraint(
            "LOWER(release_candidate_ref) NOT IN "
            "('latest', 'newest', 'current')",
            name="ck_release_candidate_evaluation_bindings_no_latest",
        ),
        Index(
            "ix_release_candidate_evaluation_bindings_selector",
            "namespace_id",
            "release_candidate_ref",
            "deployment_public_id",
            "deployment_revision",
        ),
        Index(
            "ix_release_candidate_evaluation_bindings_namespace_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_rc_eval_bindings_namespace",
            "namespace_id",
        ),
        Index(
            "ix_rc_eval_bindings_deployment",
            "deployment_id",
        ),
        Index(
            "ix_rc_eval_bindings_comparison",
            "evaluation_comparison_id",
        ),
        Index(
            "ix_rc_eval_bindings_digest",
            "binding_digest",
        ),
        Index(
            "ix_rc_eval_bindings_creator",
            "created_by_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    release_candidate_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deployment_public_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )
    deployment_revision: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    deployment_configuration_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    evaluation_comparison_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_comparisons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    binding_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    deployment: Mapped["AgentDeployment"] = relationship()
    evaluation_comparison: Mapped["EvaluationComparison"] = relationship()
    created_by_user: Mapped["User"] = relationship()
    review: Mapped["ReleaseCandidateEvaluationReview | None"] = relationship(
        back_populates="binding",
        uselist=False,
    )


class ReleaseCandidateEvaluationReview(Base):
    """One immutable human decision over exact bound release evidence."""

    __tablename__ = "release_candidate_evaluation_reviews"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_release_candidate_eval_reviews_public_id",
        ),
        UniqueConstraint(
            "binding_id",
            name="uq_release_candidate_eval_reviews_binding",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidate_eval_reviews_idempotency",
        ),
        CheckConstraint(
            "decision = 'APPROVED' OR "
            "(comment IS NOT NULL AND LENGTH(comment) >= 5)",
            name="ck_release_candidate_eval_reviews_rejection_comment",
        ),
        Index(
            "ix_rc_eval_reviews_namespace_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_rc_eval_reviews_decision_created",
            "decision",
            "created_at",
        ),
        Index("ix_rc_eval_reviews_binding", "binding_id"),
        Index("ix_rc_eval_reviews_digest", "review_digest"),
        Index("ix_rc_eval_reviews_reviewer", "reviewed_by_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey(
            "namespaces.id",
            ondelete="RESTRICT",
            name="fk_rc_eval_reviews_namespace",
        ),
        nullable=False,
    )
    binding_id: Mapped[int] = mapped_column(
        ForeignKey(
            "release_candidate_evaluation_bindings.id",
            ondelete="RESTRICT",
            name="fk_rc_eval_reviews_binding",
        ),
        nullable=False,
    )
    decision: Mapped[ReleaseCandidateReviewDecision] = mapped_column(
        Enum(
            ReleaseCandidateReviewDecision,
            name="release_candidate_review_decision",
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    review_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    reviewed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_rc_eval_reviews_reviewer",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    binding: Mapped[ReleaseCandidateEvaluationBinding] = relationship(
        back_populates="review",
    )
    reviewed_by_user: Mapped["User"] = relationship()
