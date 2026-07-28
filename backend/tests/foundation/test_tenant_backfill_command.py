"""Red-first tests for the operator-facing Foundation tenant backfill CLI.

The command is deliberately thin: parse validated operator input, restore and
atomically persist a checkpoint, delegate all domain work to FND-017, print one
deterministic report format and own the database transaction boundary.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_DIR / "scripts" / "backfill_foundation_tenants.py"


def _subject():
    if not SCRIPT_PATH.is_file():
        pytest.fail(
            "FND-017 CLI is intentionally red until "
            "backend/scripts/backfill_foundation_tenants.py exists",
            pytrace=False,
        )
    module_name = "duckdock_foundation_tenant_backfill_command"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _contracts():
    try:
        from app.services.tenant_backfill_service import BackfillCheckpoint
        from app.services.tenant_resolution_service import TenantEntityType
    except ModuleNotFoundError as exc:
        pytest.fail(f"FND-017 service contract is missing: {exc}", pytrace=False)
    return BackfillCheckpoint, TenantEntityType


@dataclass
class FakeReport:
    exit_code: int
    checkpoint: Any = None
    json_text: str = '{"format":"json"}'
    human_text: str = "tenant-backfill human summary"

    def to_json(self) -> str:
        return self.json_text

    def to_human(self) -> str:
        return self.human_text


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self):
        self.enter_count += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):  # noqa: ANN001
        self.exit_count += 1

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def scalar(self, _statement):  # noqa: ANN001
        return "20260717_0027"


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.call_count = 0

    def __call__(self) -> FakeSession:
        self.call_count += 1
        return self.session


def test_parse_args_supports_bounded_batch_dry_run_checkpoint_and_json(tmp_path):
    subject = _subject()
    checkpoint_path = tmp_path / "tenant-backfill.checkpoint.json"

    args = subject.parse_args(
        [
            "--batch-size",
            "25",
            "--dry-run",
            "--checkpoint",
            str(checkpoint_path),
            "--json",
        ]
    )

    assert args.batch_size == 25
    assert args.dry_run is True
    assert args.checkpoint == checkpoint_path
    assert args.json is True

    with pytest.raises(SystemExit) as exc:
        subject.parse_args([])
    assert exc.value.code == 2

    apply_args = subject.parse_args(
        ["--apply", "--ack-write-quiescence"]
    )
    assert isinstance(apply_args.batch_size, int) and apply_args.batch_size > 0
    assert apply_args.dry_run is False
    assert apply_args.ack_write_quiescence is True

    with pytest.raises(SystemExit) as exc:
        subject.parse_args(["--apply"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        subject.parse_args(["--dry-run", "--batch-size", "0"])
    assert exc.value.code == 2


def test_checkpoint_json_roundtrip_and_missing_file(tmp_path):
    subject = _subject()
    BackfillCheckpoint, TenantEntityType = _contracts()
    path = tmp_path / "checkpoint.json"
    checkpoint = BackfillCheckpoint(
        target_type=TenantEntityType.WORK_TRACE,
        last_id=42,
    )

    assert subject.load_checkpoint(path) is None
    subject.save_checkpoint(path, checkpoint)

    assert subject.load_checkpoint(path) == checkpoint
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "mode": "apply",
        "database_identity_sha256": subject.database_identity_sha256(),
        "alembic_revision": subject.MINIMUM_ALEMBIC_REVISION,
        "target_type": "work_trace",
        "last_id": 42,
    }


def test_checkpoint_rejects_mode_database_and_revision_mismatch(tmp_path):
    subject = _subject()
    BackfillCheckpoint, TenantEntityType = _contracts()
    path = tmp_path / "checkpoint.json"
    checkpoint = BackfillCheckpoint(
        target_type=TenantEntityType.AI_ASSET,
        last_id=7,
    )
    subject.save_checkpoint(path, checkpoint, dry_run=True)

    assert subject.load_checkpoint(path, dry_run=True) == checkpoint
    with pytest.raises(ValueError, match="mode"):
        subject.load_checkpoint(path, dry_run=False)
    with pytest.raises(ValueError, match="database"):
        subject.load_checkpoint(
            path,
            dry_run=True,
            expected_database_identity_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="revision"):
        subject.load_checkpoint(
            path,
            dry_run=True,
            expected_alembic_revision="20990101_9999",
        )


def test_checkpoint_save_atomically_replaces_in_same_directory(tmp_path, monkeypatch):
    subject = _subject()
    BackfillCheckpoint, TenantEntityType = _contracts()
    path = tmp_path / "checkpoint.json"
    path.write_text('{"old":true}', encoding="utf-8")
    checkpoint = BackfillCheckpoint(
        target_type=TenantEntityType.EVIDENCE_ITEM,
        last_id=99,
    )
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def recording_replace(source, destination):  # noqa: ANN001
        replacements.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", recording_replace)

    subject.save_checkpoint(path, checkpoint)

    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert temporary.parent.resolve() == path.parent.resolve()
    assert destination.resolve() == path.resolve()
    assert temporary != destination
    assert not temporary.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["last_id"] == 99


async def test_console_mode_commits_saves_checkpoint_and_returns_service_exit_code(
    tmp_path,
    capsys,
):
    subject = _subject()
    BackfillCheckpoint, TenantEntityType = _contracts()
    checkpoint_path = tmp_path / "checkpoint.json"
    next_checkpoint = BackfillCheckpoint(
        target_type=TenantEntityType.RUNTIME_BINDING,
        last_id=17,
    )
    report = FakeReport(exit_code=7, checkpoint=next_checkpoint)
    session = FakeSession()
    session_factory = FakeSessionFactory(session)
    calls: list[dict[str, Any]] = []

    async def service_runner(db, **kwargs):  # noqa: ANN001
        calls.append({"db": db, **kwargs})
        return report

    exit_code = await subject.async_main(
        [
            "--apply",
            "--ack-write-quiescence",
            "--batch-size",
            "31",
            "--checkpoint",
            str(checkpoint_path),
        ],
        session_factory=session_factory,
        service_runner=service_runner,
    )

    assert exit_code == 7
    assert capsys.readouterr().out == f"{report.human_text}\n"
    assert session_factory.call_count == 1
    assert session.enter_count == 1 and session.exit_count == 1
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert len(calls) == 1
    assert calls[0] == {
        "db": session,
        "batch_size": 31,
        "dry_run": False,
        "checkpoint": None,
    }
    assert subject.load_checkpoint(checkpoint_path) == next_checkpoint


async def test_json_dry_run_prints_json_and_rolls_back(capsys):
    subject = _subject()
    report = FakeReport(exit_code=3, json_text='{"conflict_count":1}')
    session = FakeSession()
    session_factory = FakeSessionFactory(session)
    calls: list[dict[str, Any]] = []

    async def service_runner(db, **kwargs):  # noqa: ANN001
        calls.append({"db": db, **kwargs})
        return report

    exit_code = await subject.async_main(
        ["--dry-run", "--json"],
        session_factory=session_factory,
        service_runner=service_runner,
    )

    assert exit_code == 3
    assert capsys.readouterr().out == '{"conflict_count":1}\n'
    assert session.commit_count == 0
    assert session.rollback_count == 1
    assert calls[0]["dry_run"] is True


async def test_service_exception_rolls_back_and_propagates():
    subject = _subject()
    session = FakeSession()
    session_factory = FakeSessionFactory(session)

    async def failing_service_runner(db, **kwargs):  # noqa: ARG001, ANN001
        raise RuntimeError("synthetic backfill failure")

    with pytest.raises(RuntimeError, match="synthetic backfill failure"):
        await subject.async_main(
            ["--apply", "--ack-write-quiescence"],
            session_factory=session_factory,
            service_runner=failing_service_runner,
        )

    assert session.commit_count == 0
    assert session.rollback_count == 1
    assert session.enter_count == 1 and session.exit_count == 1
