"""Add Release Environments, Candidates and versioned Policy decisions.

Revision ID: 20260804_0058
Revises: 20260804_0057
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_0058"
down_revision: str | None = "20260804_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str, *, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([local], [remote], name=name, ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "release_environments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "DEVELOPMENT",
                "TEST",
                "STAGING",
                "CANARY",
                "PRODUCTION",
                name="release_environment_kind",
            ),
            nullable=False,
        ),
        sa.Column("promotion_order", sa.Integer(), nullable=False),
        sa.Column("protected", sa.Boolean(), nullable=False),
        sa.Column("minimum_approvals", sa.Integer(), nullable=False),
        sa.Column("requires_canary", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "RETIRED", name="release_environment_status"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_release_environments_namespace"),
        _foreign_key("created_by_user_id", "users.id", "fk_release_environments_creator"),
        sa.UniqueConstraint("public_id", name="uq_release_environments_public_id"),
        sa.UniqueConstraint("namespace_id", "name", name="uq_release_environments_ns_name"),
        sa.UniqueConstraint(
            "namespace_id",
            "promotion_order",
            name="uq_release_environments_ns_order",
        ),
        sa.CheckConstraint(
            "promotion_order >= 0",
            name="ck_release_environments_nonnegative_order",
        ),
        sa.CheckConstraint(
            "minimum_approvals >= 0",
            name="ck_release_environments_nonnegative_approvals",
        ),
        sa.CheckConstraint(
            "protected = 1 OR minimum_approvals = 0",
            name="ck_release_environments_unprotected_no_approvals",
        ),
    )
    for name, columns in (
        ("ix_release_environments_namespace_id", ["namespace_id"]),
        ("ix_release_environments_kind", ["kind"]),
        ("ix_release_environments_status", ["status"]),
        ("ix_release_environments_created_by_user_id", ["created_by_user_id"]),
        (
            "ix_release_environments_ns_status_order",
            ["namespace_id", "status", "promotion_order"],
        ),
    ):
        op.create_index(name, "release_environments", columns)

    op.create_table(
        "release_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=1000)),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "RETIRED", name="release_policy_status"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_release_policies_namespace"),
        _foreign_key("created_by_user_id", "users.id", "fk_release_policies_creator"),
        sa.UniqueConstraint("public_id", name="uq_release_policies_public_id"),
        sa.UniqueConstraint("namespace_id", "name", name="uq_release_policies_ns_name"),
    )
    for name, columns in (
        ("ix_release_policies_namespace_id", ["namespace_id"]),
        ("ix_release_policies_status", ["status"]),
        ("ix_release_policies_created_by_user_id", ["created_by_user_id"]),
        ("ix_release_policies_ns_status_created", ["namespace_id", "status", "created_at"]),
    ):
        op.create_index(name, "release_policies", columns)

    op.create_table(
        "release_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("target_environment_id", sa.Integer(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("SHADOW", "WARN", "ENFORCE", name="release_policy_mode"),
            nullable=False,
        ),
        sa.Column("rules_json", sa.JSON(), nullable=False),
        sa.Column("rule_count", sa.Integer(), nullable=False),
        sa.Column("rules_digest", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_release_policy_versions_namespace"),
        _foreign_key("policy_id", "release_policies.id", "fk_release_policy_versions_policy"),
        _foreign_key(
            "target_environment_id",
            "release_environments.id",
            "fk_release_policy_versions_environment",
        ),
        _foreign_key("created_by_user_id", "users.id", "fk_release_policy_versions_creator"),
        sa.UniqueConstraint("public_id", name="uq_release_policy_versions_public_id"),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_release_policy_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "content_digest",
            name="uq_release_policy_versions_policy_digest",
        ),
        sa.CheckConstraint("version >= 1", name="ck_release_policy_versions_positive_version"),
        sa.CheckConstraint(
            "rule_count >= 1 AND rule_count <= 100",
            name="ck_release_policy_versions_rule_count",
        ),
    )
    for name, columns in (
        ("ix_release_policy_versions_namespace_id", ["namespace_id"]),
        ("ix_release_policy_versions_policy_id", ["policy_id"]),
        ("ix_release_policy_versions_target_environment_id", ["target_environment_id"]),
        ("ix_release_policy_versions_mode", ["mode"]),
        ("ix_release_policy_versions_created_by_user_id", ["created_by_user_id"]),
        ("ix_release_policy_versions_target_mode", ["target_environment_id", "mode"]),
        ("ix_release_policy_versions_content_digest", ["content_digest"]),
    ):
        op.create_index(name, "release_policy_versions", columns)

    op.create_table(
        "release_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("package_version_id", sa.Integer(), nullable=False),
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        sa.Column("target_environment_id", sa.Integer(), nullable=False),
        sa.Column("baseline_candidate_id", sa.Integer()),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_release_candidates_namespace"),
        _foreign_key(
            "package_version_id",
            "agent_package_versions.id",
            "fk_release_candidates_package_version",
        ),
        _foreign_key("deployment_id", "agent_deployments.id", "fk_release_candidates_deployment"),
        _foreign_key(
            "target_environment_id",
            "release_environments.id",
            "fk_release_candidates_environment",
        ),
        _foreign_key(
            "baseline_candidate_id",
            "release_candidates.id",
            "fk_release_candidates_baseline",
        ),
        _foreign_key("created_by_user_id", "users.id", "fk_release_candidates_creator"),
        sa.UniqueConstraint("public_id", name="uq_release_candidates_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidates_ns_idempotency",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "candidate_digest",
            name="uq_release_candidates_ns_digest",
        ),
        sa.UniqueConstraint(
            "package_version_id",
            "deployment_id",
            "target_environment_id",
            "baseline_candidate_id",
            name="uq_release_candidates_exact_subject",
        ),
    )
    for name, columns in (
        ("ix_release_candidates_namespace_id", ["namespace_id"]),
        ("ix_release_candidates_package_version_id", ["package_version_id"]),
        ("ix_release_candidates_deployment_id", ["deployment_id"]),
        ("ix_release_candidates_target_environment_id", ["target_environment_id"]),
        ("ix_release_candidates_baseline_candidate_id", ["baseline_candidate_id"]),
        ("ix_release_candidates_created_by_user_id", ["created_by_user_id"]),
        ("ix_release_candidates_ns_created", ["namespace_id", "created_at"]),
        ("ix_release_candidates_target_created", ["target_environment_id", "created_at"]),
        ("ix_release_candidates_digest", ["candidate_digest"]),
    ):
        op.create_index(name, "release_candidates", columns)

    op.create_table(
        "release_policy_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "raw_outcome",
            sa.Enum("PASS", "FAIL", name="release_policy_raw_outcome"),
            nullable=False,
        ),
        sa.Column(
            "enforcement_outcome",
            sa.Enum("ALLOW", "WARN", "BLOCK", name="release_policy_enforcement_outcome"),
            nullable=False,
        ),
        sa.Column("would_block", sa.Boolean(), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("decision_digest", sa.String(length=64), nullable=False),
        sa.Column("evaluation_duration_ms", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("evaluated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_release_policy_decisions_namespace"),
        _foreign_key("candidate_id", "release_candidates.id", "fk_release_policy_decisions_candidate"),
        _foreign_key(
            "policy_version_id",
            "release_policy_versions.id",
            "fk_release_policy_decisions_policy_version",
        ),
        _foreign_key("evaluated_by_user_id", "users.id", "fk_release_policy_decisions_evaluator"),
        sa.UniqueConstraint("public_id", name="uq_release_policy_decisions_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_policy_decisions_ns_idempotency",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "policy_version_id",
            "evidence_snapshot_digest",
            name="uq_release_policy_decisions_exact_evidence",
        ),
        sa.CheckConstraint(
            "evaluation_duration_ms >= 0",
            name="ck_release_policy_decisions_duration",
        ),
    )
    for name, columns in (
        ("ix_release_policy_decisions_namespace_id", ["namespace_id"]),
        ("ix_release_policy_decisions_candidate_id", ["candidate_id"]),
        ("ix_release_policy_decisions_policy_version_id", ["policy_version_id"]),
        ("ix_release_policy_decisions_raw_outcome", ["raw_outcome"]),
        ("ix_release_policy_decisions_enforcement_outcome", ["enforcement_outcome"]),
        ("ix_release_policy_decisions_evaluated_by_user_id", ["evaluated_by_user_id"]),
        ("ix_release_policy_decisions_candidate_created", ["candidate_id", "created_at"]),
        (
            "ix_release_policy_decisions_ns_outcome",
            ["namespace_id", "enforcement_outcome"],
        ),
        ("ix_release_policy_decisions_digest", ["decision_digest"]),
    ):
        op.create_index(name, "release_policy_decisions", columns)

    op.create_table(
        "release_policy_rule_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("decision_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column(
            "rule_type",
            sa.Enum(
                "PACKAGE_EVIDENCE_VERIFIED",
                "SIGNING_KEY_ACTIVE",
                "EVALUATION_GATE_PASS",
                "RISK_TIER_ALLOWED",
                "MAX_TOOL_ADDITIONS",
                "FORBID_CAPABILITY_EXPANSION",
                "MAX_VULNERABILITY_SEVERITY",
                "ROLLBACK_TARGET_REQUIRED",
                name="release_policy_rule_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "verdict",
            sa.Enum("PASS", "FAIL", "NOT_APPLICABLE", name="release_policy_rule_verdict"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("evidence_kind", sa.String(length=64), nullable=False),
        sa.Column("evidence_ref", sa.String(length=255), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "decision_id",
            "release_policy_decisions.id",
            "fk_release_policy_rule_results_decision",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "decision_id",
            "rule_id",
            name="uq_release_policy_rule_results_rule",
        ),
        sa.UniqueConstraint(
            "decision_id",
            "position",
            name="uq_release_policy_rule_results_position",
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 100",
            name="ck_release_policy_rule_results_position",
        ),
    )
    for name, columns in (
        ("ix_release_policy_rule_results_decision_id", ["decision_id"]),
        ("ix_release_policy_rule_results_rule_type", ["rule_type"]),
        ("ix_release_policy_rule_results_verdict", ["verdict"]),
        ("ix_release_policy_rule_results_type_verdict", ["rule_type", "verdict"]),
        ("ix_release_policy_rule_results_evidence_digest", ["evidence_digest"]),
    ):
        op.create_index(name, "release_policy_rule_results", columns)


def downgrade() -> None:
    raise RuntimeError(
        "0058 downgrade refused: ReleaseCandidate and PolicyDecision records are immutable governance evidence"
    )
