"""foundation tenant ownership expand

Revision ID: 20260717_0027
Revises: 20260709_0026
Create Date: 2026-07-17

Adds the nullable Namespace ownership seam required by DuckDock 2.0 to the
five existing control-plane entities.  This revision is deliberately
expand-only: it neither infers/backfills ownership nor makes the columns
non-null.  Those operations require separately reviewed revisions.

Revision 0009 creates several tables from live SQLAlchemy metadata.  A fresh
database can therefore already contain these columns by the time Alembic
reaches 0027.  The guards below make the revision safe for both fresh and
previously deployed databases.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260717_0027"
down_revision: str | None = "20260709_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TARGET_TABLES: tuple[str, ...] = (
    "runtime_instances",
    "ai_assets",
    "runtime_bindings",
    "work_traces",
    "evidence_items",
)


def _has_namespace_column(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return "namespace_id" in {
        column["name"] for column in inspector.get_columns(table_name)
    }


def _has_namespace_index(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        tuple(index.get("column_names") or ()) == ("namespace_id",)
        for index in inspector.get_indexes(table_name)
    )


def _has_namespace_fk(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        tuple(foreign_key.get("constrained_columns") or ()) == ("namespace_id",)
        and foreign_key.get("referred_table") == "namespaces"
        and tuple(foreign_key.get("referred_columns") or ()) == ("id",)
        for foreign_key in inspector.get_foreign_keys(table_name)
    )


def upgrade() -> None:
    for table_name in TARGET_TABLES:
        if not _has_namespace_column(table_name):
            op.add_column(
                table_name,
                sa.Column("namespace_id", sa.Integer(), nullable=True),
            )
        if not _has_namespace_index(table_name):
            op.create_index(
                op.f(f"ix_{table_name}_namespace_id"),
                table_name,
                ["namespace_id"],
                unique=False,
            )
        if not _has_namespace_fk(table_name):
            op.create_foreign_key(
                f"fk_{table_name}_namespace_id_namespaces",
                table_name,
                "namespaces",
                ["namespace_id"],
                ["id"],
                ondelete="RESTRICT",
            )


def _namespace_fk_names(table_name: str) -> list[str]:
    inspector = sa.inspect(op.get_bind())
    return [
        str(foreign_key["name"])
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key.get("name")
        and tuple(foreign_key.get("constrained_columns") or ())
        == ("namespace_id",)
    ]


def _namespace_index_names(table_name: str) -> list[str]:
    inspector = sa.inspect(op.get_bind())
    return [
        str(index["name"])
        for index in inspector.get_indexes(table_name)
        if index.get("name")
        and tuple(index.get("column_names") or ()) == ("namespace_id",)
    ]


def _non_null_namespace_counts() -> dict[str, int]:
    bind = op.get_bind()
    quote = bind.dialect.identifier_preparer.quote
    counts: dict[str, int] = {}
    for table_name in TARGET_TABLES:
        if not _has_namespace_column(table_name):
            continue
        statement = sa.text(
            f"SELECT COUNT(*) FROM {quote(table_name)} "
            f"WHERE {quote('namespace_id')} IS NOT NULL"
        )
        counts[table_name] = int(bind.execute(statement).scalar_one())
    return counts


def downgrade() -> None:
    """Remove only an unused expand seam; never erase assigned ownership.

    Production application backout must normally leave these additive columns
    in place.  The guarded downgrade exists for clean migration-test databases
    and refuses the operation as soon as any Namespace value has been written.
    """

    populated = {
        table_name: count
        for table_name, count in _non_null_namespace_counts().items()
        if count
    }
    if populated:
        raise RuntimeError(
            "Refusing to remove populated foundation namespace ownership: "
            f"{populated}. Roll back application routing and use a separately "
            "approved forward-fix instead."
        )

    for table_name in reversed(TARGET_TABLES):
        if not _has_namespace_column(table_name):
            continue
        for constraint_name in _namespace_fk_names(table_name):
            op.drop_constraint(
                constraint_name,
                table_name,
                type_="foreignkey",
            )
        for index_name in _namespace_index_names(table_name):
            op.drop_index(index_name, table_name=table_name)
        op.drop_column(table_name, "namespace_id")
