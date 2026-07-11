"""FK ON DELETE SET NULL for nullable runtime/asset FKs (DM-03)

Revision ID: 20260622_0019
Revises: 20260617_0018
Create Date: 2026-06-22

The 4 nullable FKs to runtime_instances / ai_assets gained ondelete="SET NULL"
in the models (fc3a22c). Fresh DBs pick that up via 0009 create_all, but EXISTING
MySQL keeps the original (implicit RESTRICT) constraints. This migration drops and
recreates those FKs with ON DELETE SET NULL. It is idempotent (skips FKs already
SET NULL — i.e. a no-op on fresh DBs) and resolves constraint names at runtime via
the inspector (MySQL auto-names them *_ibfk_N, which differ across DBs).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260622_0019"
down_revision: str | None = "20260617_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, referent_table, referent_column)
_TARGETS: tuple[tuple[str, str, str, str], ...] = (
    ("collection_jobs", "runtime_id", "runtime_instances", "id"),
    ("ai_assets", "source_runtime_id", "runtime_instances", "id"),
    ("work_traces", "runtime_id", "runtime_instances", "id"),
    ("work_traces", "asset_id", "ai_assets", "id"),
)


def _find_fk(inspector: sa.Inspector, table: str, column: str, referent: str) -> dict | None:
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") == referent and fk.get("constrained_columns") == [column]:
            return fk
    return None


def _retarget_ondelete(target_ondelete: str | None) -> None:
    bind = op.get_bind()
    # FK ondelete is only alterable on MySQL here; on SQLite/others the schema is
    # built from the (already-updated) models via create_all, so nothing to do.
    if bind.dialect.name != "mysql":
        return
    inspector = sa.inspect(bind)
    want = (target_ondelete or "").upper()
    for table, column, referent, refcol in _TARGETS:
        fk = _find_fk(inspector, table, column, referent)
        if fk is None:
            continue
        current = ((fk.get("options") or {}).get("ondelete") or "").upper()
        # Treat NO ACTION / RESTRICT / "" as "no SET NULL".
        if current == want or (want == "" and current in {"", "NO ACTION", "RESTRICT"}):
            continue
        op.drop_constraint(fk["name"], table, type_="foreignkey")
        op.create_foreign_key(
            None, table, referent, [column], [refcol], ondelete=target_ondelete
        )


def upgrade() -> None:
    _retarget_ondelete("SET NULL")


def downgrade() -> None:
    _retarget_ondelete(None)
