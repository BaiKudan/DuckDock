"""execution action idempotency guard

Revision ID: 20260617_0018
Revises: 20260616_0017
Create Date: 2026-06-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260617_0018"
down_revision: str | None = "20260616_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINT_NAME = "uq_execution_actions_case_idempotency_key"


def _unique_constraint_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {constraint["name"] for constraint in inspector.get_unique_constraints("execution_actions")}


def _clear_duplicate_idempotency_keys() -> None:
    bind = op.get_bind()
    duplicate_ids = (
        bind.execute(
            sa.text(
                """
                SELECT id
                FROM execution_actions
                WHERE idempotency_key IS NOT NULL
                  AND id NOT IN (
                    SELECT keep_id
                    FROM (
                      SELECT MIN(id) AS keep_id
                      FROM execution_actions
                      WHERE idempotency_key IS NOT NULL
                      GROUP BY handover_case_id, idempotency_key
                    ) AS kept
                  )
                """
            )
        )
        .scalars()
        .all()
    )
    if duplicate_ids:
        bind.execute(
            sa.text("UPDATE execution_actions SET idempotency_key = NULL WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": duplicate_ids},
        )


def upgrade() -> None:
    if CONSTRAINT_NAME in _unique_constraint_names():
        return
    _clear_duplicate_idempotency_keys()
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        "execution_actions",
        ["handover_case_id", "idempotency_key"],
    )


def downgrade() -> None:
    if CONSTRAINT_NAME in _unique_constraint_names():
        op.drop_constraint(CONSTRAINT_NAME, "execution_actions", type_="unique")
