"""Add approvals, exceptions, promotion receipts, canary and rollback.

Revision ID: 20260804_0059
Revises: 20260804_0058
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_0059"
down_revision: str | None = "20260804_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(local: str, remote: str, name: str, *, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([local], [remote], name=name, ondelete=ondelete)


def _indexes(table: str, definitions: tuple[tuple[str, list[str]], ...]) -> None:
    for name, columns in definitions:
        op.create_index(name, table, columns)


def upgrade() -> None:
    op.create_table(
        "release_candidate_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("policy_decision_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("APPROVED", "REJECTED", name="release_approval_decision"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum(
                "OWNER",
                "REVIEWER",
                "SECURITY",
                "OPERATIONS",
                "RELEASE_MANAGER",
                name="release_approval_role",
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=1000)),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("approval_digest", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_release_candidate_approvals_namespace"),
        _fk("candidate_id", "release_candidates.id", "fk_release_candidate_approvals_candidate"),
        _fk(
            "policy_decision_id",
            "release_policy_decisions.id",
            "fk_release_candidate_approvals_policy_decision",
        ),
        _fk("reviewed_by_user_id", "users.id", "fk_release_candidate_approvals_reviewer"),
        sa.UniqueConstraint("public_id", name="uq_release_candidate_approvals_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidate_approvals_ns_idempotency",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "policy_decision_id",
            "reviewed_by_user_id",
            name="uq_release_candidate_approvals_reviewer",
        ),
        sa.CheckConstraint(
            "decision = 'APPROVED' OR (comment IS NOT NULL AND LENGTH(comment) >= 5)",
            name="ck_release_candidate_approvals_rejection_comment",
        ),
    )
    _indexes(
        "release_candidate_approvals",
        (
            ("ix_release_candidate_approvals_namespace_id", ["namespace_id"]),
            ("ix_release_candidate_approvals_candidate_id", ["candidate_id"]),
            ("ix_release_candidate_approvals_policy_decision_id", ["policy_decision_id"]),
            ("ix_release_candidate_approvals_decision", ["decision"]),
            ("ix_release_candidate_approvals_role", ["role"]),
            ("ix_release_candidate_approvals_reviewed_by_user_id", ["reviewed_by_user_id"]),
            ("ix_release_candidate_approvals_candidate_created", ["candidate_id", "created_at"]),
            (
                "ix_release_candidate_approvals_policy_decision",
                ["policy_decision_id", "decision"],
            ),
        ),
    )

    op.create_table(
        "release_policy_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("policy_decision_id", sa.Integer(), nullable=False),
        sa.Column("waived_rule_ids_json", sa.JSON(), nullable=False),
        sa.Column("waived_rule_count", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("exception_digest", sa.String(length=64), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_release_policy_exceptions_namespace"),
        _fk("candidate_id", "release_candidates.id", "fk_release_policy_exceptions_candidate"),
        _fk(
            "policy_decision_id",
            "release_policy_decisions.id",
            "fk_release_policy_exceptions_decision",
        ),
        _fk("requested_by_user_id", "users.id", "fk_release_policy_exceptions_requester"),
        sa.UniqueConstraint("public_id", name="uq_release_policy_exceptions_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_policy_exceptions_ns_idempotency",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_release_policy_exceptions_future_expiry",
        ),
        sa.CheckConstraint(
            "waived_rule_count >= 1 AND waived_rule_count <= 100",
            name="ck_release_policy_exceptions_rule_count",
        ),
    )
    _indexes(
        "release_policy_exceptions",
        (
            ("ix_release_policy_exceptions_namespace_id", ["namespace_id"]),
            ("ix_release_policy_exceptions_candidate_id", ["candidate_id"]),
            ("ix_release_policy_exceptions_policy_decision_id", ["policy_decision_id"]),
            ("ix_release_policy_exceptions_expires_at", ["expires_at"]),
            ("ix_release_policy_exceptions_requested_by_user_id", ["requested_by_user_id"]),
            ("ix_release_policy_exceptions_decision_expiry", ["policy_decision_id", "expires_at"]),
        ),
    )

    op.create_table(
        "release_policy_exception_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("exception_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("APPROVED", "REJECTED", name="release_exception_review_decision"),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=1000)),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("review_digest", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_release_policy_exception_reviews_namespace"),
        _fk(
            "exception_id",
            "release_policy_exceptions.id",
            "fk_release_policy_exception_reviews_exception",
        ),
        _fk("reviewed_by_user_id", "users.id", "fk_release_policy_exception_reviews_reviewer"),
        sa.UniqueConstraint("public_id", name="uq_release_policy_exception_reviews_public_id"),
        sa.UniqueConstraint(
            "exception_id",
            name="uq_release_policy_exception_reviews_exception",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_policy_exception_reviews_ns_idempotency",
        ),
        sa.CheckConstraint(
            "decision = 'APPROVED' OR (comment IS NOT NULL AND LENGTH(comment) >= 5)",
            name="ck_release_policy_exception_reviews_rejection_comment",
        ),
    )
    _indexes(
        "release_policy_exception_reviews",
        (
            ("ix_release_policy_exception_reviews_namespace_id", ["namespace_id"]),
            ("ix_release_policy_exception_reviews_exception_id", ["exception_id"]),
            ("ix_release_policy_exception_reviews_decision", ["decision"]),
            ("ix_release_policy_exception_reviews_reviewed_by_user_id", ["reviewed_by_user_id"]),
            (
                "ix_release_policy_exception_reviews_decision_created",
                ["decision", "created_at"],
            ),
        ),
    )

    op.create_table(
        "release_promotions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("dispatch_public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("policy_decision_id", sa.Integer(), nullable=False),
        sa.Column("source_environment_id", sa.Integer()),
        sa.Column("target_environment_id", sa.Integer(), nullable=False),
        sa.Column(
            "strategy",
            sa.Enum("ALL_AT_ONCE", "CANARY", "ROLLING", name="release_promotion_strategy"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DISPATCHED",
                "OBSERVING",
                "SUCCEEDED",
                "FAILED",
                "ROLLBACK_REQUESTED",
                "ROLLED_BACK",
                name="release_promotion_status",
            ),
            nullable=False,
        ),
        sa.Column("acknowledge_warnings", sa.Boolean(), nullable=False),
        sa.Column("canary_config_json", sa.JSON()),
        sa.Column("exception_digest", sa.String(length=64)),
        sa.Column("approval_digest", sa.String(length=64), nullable=False),
        sa.Column("dispatch_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_release_promotions_namespace"),
        _fk("candidate_id", "release_candidates.id", "fk_release_promotions_candidate"),
        _fk(
            "policy_decision_id",
            "release_policy_decisions.id",
            "fk_release_promotions_policy_decision",
        ),
        _fk(
            "source_environment_id",
            "release_environments.id",
            "fk_release_promotions_source_environment",
        ),
        _fk(
            "target_environment_id",
            "release_environments.id",
            "fk_release_promotions_target_environment",
        ),
        _fk("requested_by_user_id", "users.id", "fk_release_promotions_requester"),
        sa.UniqueConstraint("public_id", name="uq_release_promotions_public_id"),
        sa.UniqueConstraint("dispatch_public_id", name="uq_release_promotions_dispatch_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_promotions_ns_idempotency",
        ),
        sa.CheckConstraint(
            "(strategy = 'CANARY' AND canary_config_json IS NOT NULL) OR "
            "(strategy <> 'CANARY' AND canary_config_json IS NULL)",
            name="ck_release_promotions_canary_config",
        ),
    )
    _indexes(
        "release_promotions",
        (
            ("ix_release_promotions_namespace_id", ["namespace_id"]),
            ("ix_release_promotions_candidate_id", ["candidate_id"]),
            ("ix_release_promotions_policy_decision_id", ["policy_decision_id"]),
            ("ix_release_promotions_source_environment_id", ["source_environment_id"]),
            ("ix_release_promotions_target_environment_id", ["target_environment_id"]),
            ("ix_release_promotions_strategy", ["strategy"]),
            ("ix_release_promotions_status", ["status"]),
            ("ix_release_promotions_requested_by_user_id", ["requested_by_user_id"]),
            ("ix_release_promotions_ns_status_created", ["namespace_id", "status", "created_at"]),
            ("ix_release_promotions_candidate_created", ["candidate_id", "created_at"]),
        ),
    )

    # Rollback and EnvironmentRelease form a deliberate evidence cycle. Create
    # the rollback target column first and add its FK after the history table.
    op.create_table(
        "release_rollbacks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("dispatch_public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("source_candidate_id", sa.Integer(), nullable=False),
        sa.Column("target_environment_release_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DISPATCHED", "SUCCEEDED", "FAILED", name="release_rollback_status"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("dispatch_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _fk("namespace_id", "namespaces.id", "fk_release_rollbacks_namespace"),
        _fk("promotion_id", "release_promotions.id", "fk_release_rollbacks_promotion"),
        _fk("environment_id", "release_environments.id", "fk_release_rollbacks_environment"),
        _fk("source_candidate_id", "release_candidates.id", "fk_release_rollbacks_source_candidate"),
        _fk("requested_by_user_id", "users.id", "fk_release_rollbacks_requester"),
        sa.UniqueConstraint("public_id", name="uq_release_rollbacks_public_id"),
        sa.UniqueConstraint("dispatch_public_id", name="uq_release_rollbacks_dispatch_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_rollbacks_ns_idempotency",
        ),
    )
    _indexes(
        "release_rollbacks",
        (
            ("ix_release_rollbacks_namespace_id", ["namespace_id"]),
            ("ix_release_rollbacks_promotion_id", ["promotion_id"]),
            ("ix_release_rollbacks_environment_id", ["environment_id"]),
            ("ix_release_rollbacks_source_candidate_id", ["source_candidate_id"]),
            ("ix_release_rollbacks_target_environment_release_id", ["target_environment_release_id"]),
            ("ix_release_rollbacks_status", ["status"]),
            ("ix_release_rollbacks_requested_by_user_id", ["requested_by_user_id"]),
            ("ix_release_rollbacks_promotion_status", ["promotion_id", "status"]),
        ),
    )

    op.create_table(
        "release_deployment_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("reporter_credential_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("PROMOTION", "ROLLBACK", name="release_receipt_kind"),
            nullable=False,
        ),
        sa.Column("promotion_id", sa.Integer()),
        sa.Column("rollback_id", sa.Integer()),
        sa.Column("external_receipt_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("APPLIED", "FAILED", "MISMATCH", name="release_receipt_status"),
            nullable=False,
        ),
        sa.Column("observed_package_version_public_id", sa.String(length=40), nullable=False),
        sa.Column("observed_deployment_revision", sa.String(length=128), nullable=False),
        sa.Column("observed_configuration_digest", sa.String(length=64), nullable=False),
        sa.Column("runtime_release_ref", sa.String(length=255)),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_release_deployment_receipts_namespace"),
        _fk("runtime_id", "runtime_instances.id", "fk_release_deployment_receipts_runtime"),
        _fk(
            "reporter_credential_id",
            "reporter_credentials.id",
            "fk_release_deployment_receipts_credential",
        ),
        _fk("promotion_id", "release_promotions.id", "fk_release_deployment_receipts_promotion"),
        _fk("rollback_id", "release_rollbacks.id", "fk_release_deployment_receipts_rollback"),
        sa.UniqueConstraint("public_id", name="uq_release_deployment_receipts_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "idempotency_key",
            name="uq_release_deployment_receipts_runtime_idempotency",
        ),
        sa.UniqueConstraint(
            "runtime_id",
            "external_receipt_id",
            name="uq_release_deployment_receipts_runtime_external",
        ),
        sa.CheckConstraint(
            "(kind = 'PROMOTION' AND promotion_id IS NOT NULL AND rollback_id IS NULL) OR "
            "(kind = 'ROLLBACK' AND promotion_id IS NULL AND rollback_id IS NOT NULL)",
            name="ck_release_deployment_receipts_dispatch_kind",
        ),
        sa.CheckConstraint(
            "status <> 'APPLIED' OR error_code IS NULL",
            name="ck_release_deployment_receipts_applied_no_error",
        ),
    )
    _indexes(
        "release_deployment_receipts",
        (
            ("ix_release_deployment_receipts_namespace_id", ["namespace_id"]),
            ("ix_release_deployment_receipts_runtime_id", ["runtime_id"]),
            ("ix_release_deployment_receipts_reporter_credential_id", ["reporter_credential_id"]),
            ("ix_release_deployment_receipts_kind", ["kind"]),
            ("ix_release_deployment_receipts_promotion_id", ["promotion_id"]),
            ("ix_release_deployment_receipts_rollback_id", ["rollback_id"]),
            ("ix_release_deployment_receipts_status", ["status"]),
            ("ix_release_deployment_receipts_promotion_created", ["promotion_id", "created_at"]),
            ("ix_release_deployment_receipts_rollback_created", ["rollback_id", "created_at"]),
        ),
    )

    op.create_table(
        "release_environment_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer()),
        sa.Column("rollback_id", sa.Integer()),
        sa.Column("receipt_id", sa.Integer(), nullable=False),
        sa.Column("previous_release_id", sa.Integer()),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "SUPERSEDED", "ROLLED_BACK", name="release_environment_release_status"),
            nullable=False,
        ),
        sa.Column("activation_digest", sa.String(length=64), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True)),
        _fk("namespace_id", "namespaces.id", "fk_release_environment_releases_namespace"),
        _fk("environment_id", "release_environments.id", "fk_release_environment_releases_environment"),
        _fk("candidate_id", "release_candidates.id", "fk_release_environment_releases_candidate"),
        _fk("promotion_id", "release_promotions.id", "fk_release_environment_releases_promotion"),
        _fk("rollback_id", "release_rollbacks.id", "fk_release_environment_releases_rollback"),
        _fk("receipt_id", "release_deployment_receipts.id", "fk_release_environment_releases_receipt"),
        _fk(
            "previous_release_id",
            "release_environment_releases.id",
            "fk_release_environment_releases_previous",
        ),
        sa.UniqueConstraint("public_id", name="uq_release_environment_releases_public_id"),
        sa.UniqueConstraint("receipt_id", name="uq_release_environment_releases_receipt"),
        sa.CheckConstraint(
            "(promotion_id IS NOT NULL AND rollback_id IS NULL) OR "
            "(promotion_id IS NULL AND rollback_id IS NOT NULL)",
            name="ck_release_environment_releases_activation_source",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND deactivated_at IS NULL) OR "
            "(status IN ('SUPERSEDED', 'ROLLED_BACK') AND deactivated_at IS NOT NULL)",
            name="ck_release_environment_releases_status_time",
        ),
    )
    _indexes(
        "release_environment_releases",
        (
            ("ix_release_environment_releases_namespace_id", ["namespace_id"]),
            ("ix_release_environment_releases_environment_id", ["environment_id"]),
            ("ix_release_environment_releases_candidate_id", ["candidate_id"]),
            ("ix_release_environment_releases_promotion_id", ["promotion_id"]),
            ("ix_release_environment_releases_rollback_id", ["rollback_id"]),
            ("ix_release_environment_releases_receipt_id", ["receipt_id"]),
            ("ix_release_environment_releases_previous_release_id", ["previous_release_id"]),
            ("ix_release_environment_releases_status", ["status"]),
            ("ix_release_environment_releases_environment_status", ["environment_id", "status"]),
            ("ix_release_environment_releases_candidate_activated", ["candidate_id", "activated_at"]),
        ),
    )
    op.create_foreign_key(
        "fk_release_rollbacks_target_release",
        "release_rollbacks",
        "release_environment_releases",
        ["target_environment_release_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "release_canary_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum("PASS", "FAIL", "INCONCLUSIVE", name="release_canary_outcome"),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_run_count", sa.Integer(), nullable=False),
        sa.Column("failed_run_count", sa.Integer(), nullable=False),
        sa.Column("untrusted_run_count", sa.Integer(), nullable=False),
        sa.Column("failure_rate", sa.Float(), nullable=False),
        sa.Column("untrusted_rate", sa.Float(), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("decision_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("evaluated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_release_canary_evaluations_namespace"),
        _fk("promotion_id", "release_promotions.id", "fk_release_canary_evaluations_promotion"),
        _fk("evaluated_by_user_id", "users.id", "fk_release_canary_evaluations_evaluator"),
        sa.UniqueConstraint("public_id", name="uq_release_canary_evaluations_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_canary_evaluations_ns_idempotency",
        ),
        sa.UniqueConstraint(
            "promotion_id",
            "evidence_digest",
            name="uq_release_canary_evaluations_evidence",
        ),
        sa.CheckConstraint(
            "completed_run_count >= 0",
            name="ck_release_canary_evaluations_run_count",
        ),
        sa.CheckConstraint(
            "failure_rate >= 0 AND failure_rate <= 1 AND "
            "untrusted_rate >= 0 AND untrusted_rate <= 1",
            name="ck_release_canary_evaluations_rates",
        ),
        sa.CheckConstraint(
            "window_end > window_start",
            name="ck_release_canary_evaluations_window",
        ),
    )
    _indexes(
        "release_canary_evaluations",
        (
            ("ix_release_canary_evaluations_namespace_id", ["namespace_id"]),
            ("ix_release_canary_evaluations_promotion_id", ["promotion_id"]),
            ("ix_release_canary_evaluations_outcome", ["outcome"]),
            ("ix_release_canary_evaluations_evaluated_by_user_id", ["evaluated_by_user_id"]),
            ("ix_release_canary_evaluations_promotion_created", ["promotion_id", "created_at"]),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "0059 downgrade refused: approvals, Runtime receipts, activation and rollback evidence are immutable"
    )
