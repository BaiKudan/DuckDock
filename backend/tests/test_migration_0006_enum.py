"""迁移 0006 枚举去 Postgres 化测试（DM-04）。

项目是 MySQL-only：0006 迁移里的 postgresql.ENUM(...) 是死代码（仅在 Postgres 上有别）。
本测试断言迁移已改用 sa.Enum(...)，且不再引用 postgresql.ENUM，同时保持相同的枚举名/值
（即产出的 MySQL DDL 不变）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260413_0006_governance_release_gate.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0006", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_source_uses_sa_enum_not_postgresql_enum():
    source = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert "postgresql.ENUM" not in source
    assert "from sqlalchemy.dialects import postgresql" not in source
    assert "sa.Enum(" in source


def test_module_level_enums_are_generic_sa_enum_with_same_names_and_values():
    module = _load_migration_module()

    expected = {
        "skill_version_review_status_enum": (
            "skillversionreviewstatus",
            ("NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED"),
        ),
        "public_release_approval_status_enum": (
            "publicreleaseapprovalstatus",
            ("PENDING", "APPROVED", "REJECTED"),
        ),
        "employment_status_enum": (
            "employmentstatus",
            ("ACTIVE", "ONBOARDING", "LEAVE", "OFFBOARDED"),
        ),
    }

    for attr, (name, values) in expected.items():
        enum_type = getattr(module, attr)
        # 通用 sa.Enum，而不是 postgres 方言枚举
        assert isinstance(enum_type, sa.Enum)
        assert type(enum_type).__module__.startswith("sqlalchemy.sql.")
        # 名称与取值集合保持不变 → MySQL DDL 不变
        assert enum_type.name == name
        assert tuple(enum_type.enums) == values
