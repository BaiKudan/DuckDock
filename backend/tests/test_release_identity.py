from __future__ import annotations

import json
from pathlib import Path

from app.contracts.openapi_v2 import CONTRACT_VERSION
from app.main import app
from app.version import DUCKDOCK_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_release_candidate_identity_is_consistent_across_artifacts() -> None:
    expected = "2.0.0-rc.1"
    frontend_package = json.loads(
        (PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    generated_contract = json.loads(
        (
            PROJECT_ROOT
            / "specs"
            / "015-ga-candidate"
            / "contracts"
            / "openapi-v2.generated.json"
        ).read_text(encoding="utf-8")
    )

    assert DUCKDOCK_VERSION == expected
    assert app.version == expected
    assert CONTRACT_VERSION == expected
    assert frontend_package["version"] == expected
    assert generated_contract["info"]["version"] == expected
    for dockerfile in (
        PROJECT_ROOT / "backend" / "Dockerfile",
        PROJECT_ROOT / "frontend" / "Dockerfile.prod",
    ):
        assert f"ARG DUCKDOCK_VERSION={expected}" in dockerfile.read_text(
            encoding="utf-8"
        )
