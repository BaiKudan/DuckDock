from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_DIR / "scripts" / "remediate_foundation_tenants.py"


def _subject():
    assert SCRIPT_PATH.is_file()
    name = "duckdock_tenant_remediation_command"
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class FakeReport:
    json_text: str = '{"changed_count":1}'
    human_text: str = "tenant-remediation changed=1"

    def to_json(self) -> str:
        return self.json_text

    def to_human(self) -> str:
        return self.human_text


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def scalar(self, _statement):
        return "20260720_0028"


def _manifest_payload() -> dict:
    return {
        "schema_version": 1,
        "manifest_id": "S1-D:command",
        "change_ticket": "CHANGE-3",
        "reason": "Explicit approved historical ownership",
        "approved_by_user_id": 1,
        "assignments": [
            {"target_type": "ai_asset", "target_id": 1, "namespace_id": 1},
        ],
    }


def test_parse_args_requires_mode_and_apply_ack(tmp_path):
    subject = _subject()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")

    dry = subject.parse_args(["--manifest", str(path), "--dry-run", "--json"])
    assert dry.dry_run is True
    assert dry.json is True

    with pytest.raises(SystemExit):
        subject.parse_args(["--manifest", str(path), "--apply"])


def test_load_manifest_rejects_invalid_json_and_unknown_fields(tmp_path):
    subject = _subject()
    path = tmp_path / "manifest.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read"):
        subject.load_manifest(path)

    payload = {**_manifest_payload(), "default_namespace_id": 1}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid tenant remediation manifest"):
        subject.load_manifest(path)


async def test_async_main_owns_dry_run_and_apply_transactions(tmp_path, capsys):
    subject = _subject()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")
    calls = []

    async def runner(db, *, manifest, dry_run):
        calls.append((db, manifest.manifest_id, dry_run))
        return FakeReport()

    dry_session = FakeSession()
    dry_exit = await subject.async_main(
        ["--manifest", str(path), "--dry-run", "--json"],
        session_factory=lambda: dry_session,
        service_runner=runner,
    )
    assert dry_exit == 0
    assert dry_session.rollback_count == 1
    assert dry_session.commit_count == 0
    assert capsys.readouterr().out == '{"changed_count":1}\n'

    apply_session = FakeSession()
    apply_exit = await subject.async_main(
        [
            "--manifest",
            str(path),
            "--apply",
            "--ack-write-quiescence",
        ],
        session_factory=lambda: apply_session,
        service_runner=runner,
    )
    assert apply_exit == 0
    assert apply_session.commit_count == 1
    assert apply_session.rollback_count == 0
    assert capsys.readouterr().out == "tenant-remediation changed=1\n"
    assert [call[2] for call in calls] == [True, False]
