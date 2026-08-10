"""Add evidence-driven Handover 2.0 snapshots, obligations and signed packages.

Revision ID: 20260804_0060
Revises: 20260804_0059
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_0060"
down_revision: str | None = "20260804_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(local: str, remote: str, name: str, *, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([local], [remote], name=name, ondelete=ondelete)


def _indexes(table: str, definitions: tuple[tuple[str, list[str]], ...]) -> None:
    for name, columns in definitions:
        op.create_index(name, table, columns)


def upgrade() -> None:
    op.add_column("handover_cases", sa.Column("fallback_owner_user_id", sa.Integer()))
    op.create_foreign_key(
        "fk_handover_cases_fallback_owner",
        "handover_cases",
        "users",
        ["fallback_owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_handover_cases_fallback_owner_user_id",
        "handover_cases",
        ["fallback_owner_user_id"],
    )

    op.create_table(
        "handover_evidence_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("handover_case_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("subject_json", sa.JSON(), nullable=False),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("edges_json", sa.JSON(), nullable=False),
        sa.Column("evidence_summary_json", sa.JSON(), nullable=False),
        sa.Column("readiness_json", sa.JSON(), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("edge_count", sa.Integer(), nullable=False),
        sa.Column("check_count", sa.Integer(), nullable=False),
        sa.Column(
            "readiness_outcome",
            sa.Enum("READY", "BLOCKED", name="handover_readiness_outcome"),
            nullable=False,
        ),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_handover_evidence_snapshots_namespace"),
        _fk("handover_case_id", "handover_cases.id", "fk_handover_evidence_snapshots_case"),
        _fk("created_by_user_id", "users.id", "fk_handover_evidence_snapshots_creator"),
        sa.UniqueConstraint("public_id", name="uq_handover_evidence_snapshots_public_id"),
        sa.UniqueConstraint(
            "handover_case_id",
            "sequence",
            name="uq_handover_evidence_snapshots_case_sequence",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_evidence_snapshots_ns_idempotency",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_handover_evidence_snapshots_positive_sequence"),
        sa.CheckConstraint("node_count >= 1", name="ck_handover_evidence_snapshots_node_count"),
        sa.CheckConstraint("edge_count >= 0", name="ck_handover_evidence_snapshots_edge_count"),
        sa.CheckConstraint("check_count >= 1", name="ck_handover_evidence_snapshots_check_count"),
    )
    _indexes(
        "handover_evidence_snapshots",
        (
            ("ix_handover_evidence_snapshots_namespace_id", ["namespace_id"]),
            ("ix_handover_evidence_snapshots_handover_case_id", ["handover_case_id"]),
            ("ix_handover_evidence_snapshots_readiness_outcome", ["readiness_outcome"]),
            ("ix_handover_evidence_snapshots_snapshot_digest", ["snapshot_digest"]),
            ("ix_handover_evidence_snapshots_created_by_user_id", ["created_by_user_id"]),
            (
                "ix_handover_evidence_snapshots_case_created",
                ["handover_case_id", "created_at"],
            ),
            (
                "ix_handover_evidence_snapshots_ns_outcome_created",
                ["namespace_id", "readiness_outcome", "created_at"],
            ),
        ),
    )

    op.create_table(
        "handover_obligations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("handover_case_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("obligation_key", sa.String(length=255), nullable=False),
        sa.Column(
            "obligation_type",
            sa.Enum(
                "RECEIVER_ACCESS",
                "OWNER_OR_FALLBACK",
                "PRODUCTION_VERSION",
                "EVALUATION_BASELINE",
                "RUNBOOK",
                "RISK_EVIDENCE",
                "FAILED_ACTION_ACKNOWLEDGEMENT",
                name="handover_obligation_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("BLOCKING", "ADVISORY", name="handover_obligation_severity"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("requirement_json", sa.JSON(), nullable=False),
        sa.Column("requires_evidence", sa.Boolean(), nullable=False),
        sa.Column("obligation_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_handover_obligations_namespace"),
        _fk("handover_case_id", "handover_cases.id", "fk_handover_obligations_case"),
        _fk("snapshot_id", "handover_evidence_snapshots.id", "fk_handover_obligations_snapshot"),
        sa.UniqueConstraint("public_id", name="uq_handover_obligations_public_id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "obligation_key",
            name="uq_handover_obligations_snapshot_key",
        ),
    )
    _indexes(
        "handover_obligations",
        (
            ("ix_handover_obligations_namespace_id", ["namespace_id"]),
            ("ix_handover_obligations_handover_case_id", ["handover_case_id"]),
            ("ix_handover_obligations_snapshot_id", ["snapshot_id"]),
            ("ix_handover_obligations_obligation_type", ["obligation_type"]),
            ("ix_handover_obligations_severity", ["severity"]),
            ("ix_handover_obligations_case_severity", ["handover_case_id", "severity"]),
        ),
    )

    op.create_table(
        "handover_obligation_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("obligation_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("FULFILLED", "FAILED", "WAIVED", name="handover_obligation_receipt_decision"),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_handover_obligation_receipts_namespace"),
        _fk("obligation_id", "handover_obligations.id", "fk_handover_obligation_receipts_obligation"),
        _fk("decided_by_user_id", "users.id", "fk_handover_obligation_receipts_actor"),
        sa.UniqueConstraint("public_id", name="uq_handover_obligation_receipts_public_id"),
        sa.UniqueConstraint("obligation_id", name="uq_handover_obligation_receipts_obligation"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_obligation_receipts_ns_idempotency",
        ),
    )
    _indexes(
        "handover_obligation_receipts",
        (
            ("ix_handover_obligation_receipts_namespace_id", ["namespace_id"]),
            ("ix_handover_obligation_receipts_obligation_id", ["obligation_id"]),
            ("ix_handover_obligation_receipts_decision", ["decision"]),
            ("ix_handover_obligation_receipts_decided_by_user_id", ["decided_by_user_id"]),
        ),
    )

    op.create_table(
        "handover_acceptances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("handover_case_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("ACCEPTED", "REJECTED", name="handover_acceptance_decision"),
            nullable=False,
        ),
        sa.Column("acknowledges_failures", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=False),
        sa.Column("obligation_receipt_digests_json", sa.JSON(), nullable=False),
        sa.Column("acceptance_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("accepted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_handover_acceptances_namespace"),
        _fk("handover_case_id", "handover_cases.id", "fk_handover_acceptances_case"),
        _fk("snapshot_id", "handover_evidence_snapshots.id", "fk_handover_acceptances_snapshot"),
        _fk("accepted_by_user_id", "users.id", "fk_handover_acceptances_actor"),
        sa.UniqueConstraint("public_id", name="uq_handover_acceptances_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_acceptances_ns_idempotency",
        ),
    )
    _indexes(
        "handover_acceptances",
        (
            ("ix_handover_acceptances_namespace_id", ["namespace_id"]),
            ("ix_handover_acceptances_handover_case_id", ["handover_case_id"]),
            ("ix_handover_acceptances_snapshot_id", ["snapshot_id"]),
            ("ix_handover_acceptances_decision", ["decision"]),
            ("ix_handover_acceptances_accepted_by_user_id", ["accepted_by_user_id"]),
            ("ix_handover_acceptances_snapshot_created", ["snapshot_id", "created_at"]),
        ),
    )

    op.create_table(
        "handover_signed_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("handover_case_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("acceptance_id", sa.Integer(), nullable=False),
        sa.Column("evidence_item_id", sa.Integer(), nullable=False),
        sa.Column("signing_key_id", sa.Integer(), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("archive_digest", sa.String(length=64), nullable=False),
        sa.Column("signature_algorithm", sa.String(length=32), nullable=False),
        sa.Column("signature_value", sa.String(length=512), nullable=False),
        sa.Column("signature_digest", sa.String(length=64), nullable=False),
        sa.Column("attestation_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("namespace_id", "namespaces.id", "fk_handover_signed_packages_namespace"),
        _fk("handover_case_id", "handover_cases.id", "fk_handover_signed_packages_case"),
        _fk("snapshot_id", "handover_evidence_snapshots.id", "fk_handover_signed_packages_snapshot"),
        _fk("acceptance_id", "handover_acceptances.id", "fk_handover_signed_packages_acceptance"),
        _fk("evidence_item_id", "evidence_items.id", "fk_handover_signed_packages_evidence"),
        _fk("signing_key_id", "package_signing_keys.id", "fk_handover_signed_packages_signing_key"),
        _fk("created_by_user_id", "users.id", "fk_handover_signed_packages_creator"),
        sa.UniqueConstraint("public_id", name="uq_handover_signed_packages_public_id"),
        sa.UniqueConstraint("acceptance_id", name="uq_handover_signed_packages_acceptance"),
        sa.UniqueConstraint("evidence_item_id", name="uq_handover_signed_packages_evidence"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_handover_signed_packages_ns_idempotency",
        ),
    )
    _indexes(
        "handover_signed_packages",
        (
            ("ix_handover_signed_packages_namespace_id", ["namespace_id"]),
            ("ix_handover_signed_packages_handover_case_id", ["handover_case_id"]),
            ("ix_handover_signed_packages_snapshot_id", ["snapshot_id"]),
            ("ix_handover_signed_packages_acceptance_id", ["acceptance_id"]),
            ("ix_handover_signed_packages_evidence_item_id", ["evidence_item_id"]),
            ("ix_handover_signed_packages_signing_key_id", ["signing_key_id"]),
            ("ix_handover_signed_packages_created_by_user_id", ["created_by_user_id"]),
            ("ix_handover_signed_packages_case_created", ["handover_case_id", "created_at"]),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "0060 downgrade refused: Handover snapshots, obligations, acceptances and signatures are immutable evidence"
    )
