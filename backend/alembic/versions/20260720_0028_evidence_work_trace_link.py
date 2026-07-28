"""add typed EvidenceItem to WorkTrace link

Revision ID: 20260720_0028
Revises: 20260717_0027
Create Date: 2026-07-20

This is an expand-only migration. The nullable typed link enables
deterministic Evidence tenant resolution without guessing from collection
jobs, URIs, users, or JSON metadata. Tenant contract/non-null work remains
deferred to FND-019.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260720_0028"
down_revision: str | None = "20260717_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "evidence_items"
COLUMN = "work_trace_id"


def _has_column() -> bool:
    return COLUMN in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)
    }


def _has_index() -> bool:
    return any(
        tuple(index.get("column_names") or ()) == (COLUMN,)
        for index in sa.inspect(op.get_bind()).get_indexes(TABLE)
    )


def _has_fk() -> bool:
    return any(
        tuple(foreign_key.get("constrained_columns") or ()) == (COLUMN,)
        and foreign_key.get("referred_table") == "work_traces"
        and tuple(foreign_key.get("referred_columns") or ()) == ("id",)
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(TABLE)
    )


def _non_null_link_count() -> int:
    if not _has_column():
        return 0
    evidence_items = sa.table(TABLE, sa.column(COLUMN, sa.Integer()))
    return int(
        op.get_bind().execute(
            sa.select(sa.func.count()).select_from(evidence_items).where(
                evidence_items.c.work_trace_id.is_not(None)
            )
        ).scalar_one()
    )


def upgrade() -> None:
    if not _has_column():
        op.add_column(TABLE, sa.Column(COLUMN, sa.Integer(), nullable=True))
    if not _has_index():
        op.create_index(op.f("ix_evidence_items_work_trace_id"), TABLE, [COLUMN], unique=False)
    if not _has_fk():
        op.create_foreign_key(
            "fk_evidence_items_work_trace_id_work_traces",
            TABLE,
            "work_traces",
            [COLUMN],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if not _has_column():
        return
    assigned_count = _non_null_link_count()
    if assigned_count:
        raise RuntimeError(
            "Refusing to remove populated EvidenceItem.work_trace_id typed links: "
            f"{assigned_count} row(s) must be remediated first"
        )
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys(TABLE):
        if foreign_key.get("name") and tuple(foreign_key.get("constrained_columns") or ()) == (COLUMN,):
            op.drop_constraint(foreign_key["name"], TABLE, type_="foreignkey")
    for index in inspector.get_indexes(TABLE):
        if index.get("name") and tuple(index.get("column_names") or ()) == (COLUMN,):
            op.drop_index(index["name"], table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
