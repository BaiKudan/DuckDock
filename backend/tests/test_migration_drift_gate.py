from pathlib import Path

from app.models.control_plane import ExecutionAction
from app.models.control_plane import HandoverItem


def test_execution_mode_model_default_matches_migration_server_default():
    default = ExecutionAction.__table__.c.execution_mode.server_default
    assert default is not None
    assert str(default.arg) == "MANUAL"


def test_handover_item_confidence_default_matches_mysql_reflection():
    default = HandoverItem.__table__.c.confidence.server_default
    assert default is not None
    assert str(default.arg) == "'0'"


def test_execution_action_idempotency_unique_constraint_is_modelled():
    constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in ExecutionAction.__table__.constraints
    }
    assert constraints["uq_execution_actions_case_idempotency_key"] == (
        "handover_case_id",
        "idempotency_key",
    )


def test_baseline_migration_is_static_ddl():
    migration = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20260409_0001_baseline_schema.py"
    text = migration.read_text(encoding="utf-8")
    assert "Base.metadata.create_all" not in text
    assert "Base.metadata.drop_all" not in text
    assert "op.create_table(" in text


def test_ci_runs_alembic_check_after_upgrade():
    ci = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    text = ci.read_text(encoding="utf-8")
    assert "python -m alembic -c alembic.ini upgrade head" in text
    assert "python -m alembic -c alembic.ini check" in text
