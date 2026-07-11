from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.v1.endpoints.registry import _version_updated_at


def test_version_updated_at_normalizes_naive_database_timestamps():
    version = SimpleNamespace(created_at=datetime(2026, 5, 23, 1, 0, 0))
    scan = SimpleNamespace(
        created_at=datetime(2026, 5, 23, 1, 5, 0),
        started_at=datetime(2026, 5, 23, 1, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 5, 23, 1, 15, 0),
    )

    updated_at = _version_updated_at(version, scan)

    assert updated_at == datetime(2026, 5, 23, 1, 15, 0, tzinfo=timezone.utc)
