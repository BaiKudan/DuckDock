"""contract Foundation tenant ownership

Revision ID: 20260728_0029
Revises: 20260720_0028
Create Date: 2026-07-28

The revision is the Contract step of Expand -> Backfill -> Contract. It runs
the complete read-only blocker inventory before any DDL, then makes the five
direct Namespace keys non-null and adds tenant-first access paths.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_0029"
down_revision: str | None = "20260720_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TARGET_TABLES: tuple[str, ...] = (
    "runtime_instances",
    "ai_assets",
    "runtime_bindings",
    "work_traces",
    "evidence_items",
)
TENANT_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "runtime_instances": (
        "ix_runtime_instances_namespace_status_updated_at",
        ("namespace_id", "status", "updated_at"),
    ),
    "ai_assets": (
        "ix_ai_assets_namespace_status_last_seen_at",
        ("namespace_id", "status", "last_seen_at"),
    ),
    "work_traces": (
        "ix_work_traces_namespace_started_at",
        ("namespace_id", "started_at"),
    ),
    "evidence_items": (
        "ix_evidence_items_namespace_created_at",
        ("namespace_id", "created_at"),
    ),
}
BINDING_UNIQUE_NAME = (
    "uq_runtime_bindings_namespace_runtime_asset_environment"
)
BINDING_UNIQUE_COLUMNS = (
    "namespace_id",
    "runtime_id",
    "asset_id",
    "environment",
)
REMEDIATION_COMMAND = (
    "python scripts/check_foundation_tenant_contract.py --json; "
    "python scripts/remediate_foundation_tenants.py "
    "--manifest <approved-manifest.json> --dry-run --json"
)


def _count(statement: str) -> int:
    return int(op.get_bind().execute(sa.text(statement)).scalar_one())


def _contract_blockers() -> dict[str, dict[str, int]]:
    bind = op.get_bind()
    quote = bind.dialect.identifier_preparer.quote
    null_counts = {
        table_name: _count(
            f"SELECT COUNT(*) FROM {quote(table_name)} "
            f"WHERE {quote('namespace_id')} IS NULL"
        )
        for table_name in TARGET_TABLES
    }
    relationship_counts = {
        "runtime_binding_runtime_missing_or_mismatch": _count(
            """
            SELECT COUNT(*)
            FROM runtime_bindings AS binding
            LEFT JOIN runtime_instances AS runtime
              ON runtime.id = binding.runtime_id
            WHERE binding.namespace_id IS NOT NULL
              AND (
                runtime.id IS NULL
                OR runtime.namespace_id IS NULL
                OR runtime.namespace_id <> binding.namespace_id
              )
            """
        ),
        "runtime_binding_asset_missing_or_mismatch": _count(
            """
            SELECT COUNT(*)
            FROM runtime_bindings AS binding
            LEFT JOIN ai_assets AS asset
              ON asset.id = binding.asset_id
            WHERE binding.namespace_id IS NOT NULL
              AND (
                asset.id IS NULL
                OR asset.namespace_id IS NULL
                OR asset.namespace_id <> binding.namespace_id
              )
            """
        ),
        "work_trace_runtime_missing_or_mismatch": _count(
            """
            SELECT COUNT(*)
            FROM work_traces AS trace
            LEFT JOIN runtime_instances AS runtime
              ON runtime.id = trace.runtime_id
            WHERE trace.namespace_id IS NOT NULL
              AND trace.runtime_id IS NOT NULL
              AND (
                runtime.id IS NULL
                OR runtime.namespace_id IS NULL
                OR runtime.namespace_id <> trace.namespace_id
              )
            """
        ),
        "work_trace_asset_missing_or_mismatch": _count(
            """
            SELECT COUNT(*)
            FROM work_traces AS trace
            LEFT JOIN ai_assets AS asset
              ON asset.id = trace.asset_id
            WHERE trace.namespace_id IS NOT NULL
              AND trace.asset_id IS NOT NULL
              AND (
                asset.id IS NULL
                OR asset.namespace_id IS NULL
                OR asset.namespace_id <> trace.namespace_id
              )
            """
        ),
        "evidence_work_trace_missing_or_mismatch": _count(
            """
            SELECT COUNT(*)
            FROM evidence_items AS evidence
            LEFT JOIN work_traces AS trace
              ON trace.id = evidence.work_trace_id
            WHERE evidence.namespace_id IS NOT NULL
              AND evidence.work_trace_id IS NOT NULL
              AND (
                trace.id IS NULL
                OR trace.namespace_id IS NULL
                OR trace.namespace_id <> evidence.namespace_id
              )
            """
        ),
        "runtime_binding_tenant_key_duplicates": _count(
            """
            SELECT COUNT(*)
            FROM (
              SELECT 1
              FROM runtime_bindings
              WHERE namespace_id IS NOT NULL
              GROUP BY namespace_id, runtime_id, asset_id, environment
              HAVING COUNT(*) > 1
            ) AS duplicate_binding_keys
            """
        ),
    }
    return {
        "null_counts": null_counts,
        "relationship_counts": relationship_counts,
    }


def _total_blockers(blockers: dict[str, dict[str, int]]) -> int:
    return sum(
        count
        for category in blockers.values()
        for count in category.values()
    )


def _is_namespace_nullable(table_name: str) -> bool:
    columns = {
        str(column["name"]): column
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    return bool(columns["namespace_id"]["nullable"])


def _has_index(table_name: str, columns: tuple[str, ...]) -> bool:
    return any(
        tuple(index.get("column_names") or ()) == columns
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    )


def _has_binding_unique() -> bool:
    inspector = sa.inspect(op.get_bind())
    if any(
        tuple(constraint.get("column_names") or ())
        == BINDING_UNIQUE_COLUMNS
        for constraint in inspector.get_unique_constraints("runtime_bindings")
    ):
        return True
    return any(
        bool(index.get("unique"))
        and tuple(index.get("column_names") or ())
        == BINDING_UNIQUE_COLUMNS
        for index in inspector.get_indexes("runtime_bindings")
    )


def upgrade() -> None:
    blockers = _contract_blockers()
    total_blockers = _total_blockers(blockers)
    if total_blockers:
        report = {
            **blockers,
            "total_blockers": total_blockers,
            "remediation_command": REMEDIATION_COMMAND,
        }
        raise RuntimeError(
            "Foundation tenant contract blocked: "
            + json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    for table_name in TARGET_TABLES:
        if _is_namespace_nullable(table_name):
            op.alter_column(
                table_name,
                "namespace_id",
                existing_type=sa.Integer(),
                nullable=False,
            )

    for table_name, (index_name, columns) in TENANT_INDEXES.items():
        if not _has_index(table_name, columns):
            op.create_index(
                index_name,
                table_name,
                list(columns),
                unique=False,
            )

    if not _has_binding_unique():
        op.create_unique_constraint(
            BINDING_UNIQUE_NAME,
            "runtime_bindings",
            list(BINDING_UNIQUE_COLUMNS),
        )


def downgrade() -> None:
    if _has_binding_unique():
        op.drop_constraint(
            BINDING_UNIQUE_NAME,
            "runtime_bindings",
            type_="unique",
        )

    for table_name, (index_name, columns) in reversed(
        tuple(TENANT_INDEXES.items())
    ):
        if _has_index(table_name, columns):
            op.drop_index(index_name, table_name=table_name)

    for table_name in reversed(TARGET_TABLES):
        if not _is_namespace_nullable(table_name):
            op.alter_column(
                table_name,
                "namespace_id",
                existing_type=sa.Integer(),
                nullable=True,
            )
