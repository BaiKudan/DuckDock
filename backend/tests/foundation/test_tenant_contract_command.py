from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_DIR / "scripts" / "check_foundation_tenant_contract.py"


def _subject():
    assert SCRIPT_PATH.is_file()
    name = "duckdock_tenant_contract_command"
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class FakeReport:
    exit_code: int = 2

    def to_json(self) -> str:
        return '{"total_blockers":4}'

    def to_human(self) -> str:
        return "tenant-contract total_blockers=4"


class FakeSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def scalar(self, _statement):
        return "20260720_0028"


async def test_contract_command_is_read_only_and_returns_report_exit_code(capsys):
    subject = _subject()
    session = FakeSession()

    async def checker(db):
        assert db is session
        return FakeReport()

    exit_code = await subject.async_main(
        ["--json"],
        session_factory=lambda: session,
        checker=checker,
    )

    assert exit_code == 2
    assert session.rollback_count == 1
    assert capsys.readouterr().out == '{"total_blockers":4}\n'
