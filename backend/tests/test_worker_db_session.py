from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.workers import analysis_tasks
from app.workers import clinic_tasks
from app.workers import db as worker_db
from app.workers import lifecycle_tasks
from app.workers import report_upload_tasks
from app.workers import scan_tasks
from app.workers import webhook_tasks


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False
        self.echo: bool | None = None

    async def dispose(self) -> None:
        self.disposed = True


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


def install_fake_engine(monkeypatch: pytest.MonkeyPatch) -> list[FakeEngine]:
    engines: list[FakeEngine] = []

    def fake_create_async_engine(url: str, *, echo: bool = False) -> FakeEngine:
        engine = FakeEngine()
        engine.echo = echo
        engines.append(engine)
        return engine

    def fake_async_sessionmaker(engine: FakeEngine, *, expire_on_commit: bool):
        assert engine is engines[-1]
        assert expire_on_commit is False
        return lambda: FakeSession()

    monkeypatch.setattr(worker_db, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(worker_db, "async_sessionmaker", fake_async_sessionmaker)
    return engines


@pytest.mark.asyncio
async def test_worker_db_session_disposes_engine_after_success(monkeypatch: pytest.MonkeyPatch):
    engines = install_fake_engine(monkeypatch)

    async with worker_db.worker_db_session(echo=True) as session:
        assert isinstance(session, FakeSession)

    assert len(engines) == 1
    assert engines[0].echo is True
    assert engines[0].disposed is True


@pytest.mark.asyncio
async def test_worker_db_session_disposes_engine_after_error(monkeypatch: pytest.MonkeyPatch):
    engines = install_fake_engine(monkeypatch)

    with pytest.raises(RuntimeError, match="retry path"):
        async with worker_db.worker_db_session() as session:
            assert isinstance(session, FakeSession)
            raise RuntimeError("retry path")

    assert len(engines) == 1
    assert engines[0].disposed is True


@pytest.mark.parametrize(
    "module",
    [
        analysis_tasks,
        clinic_tasks,
        lifecycle_tasks,
        report_upload_tasks,
        scan_tasks,
        webhook_tasks,
    ],
)
def test_celery_workers_use_managed_db_session(module: Any):
    source = inspect.getsource(module)

    assert "worker_db_session" in source
    assert "create_async_engine" not in source
    assert "engine.dispose" not in source
