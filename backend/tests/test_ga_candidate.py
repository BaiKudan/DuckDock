from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.contracts.openapi_v2 import (
    CONTRACT_VERSION,
    EXPECTED_CONTRACT_SHA256,
    build_openapi_v2_contract,
    render_openapi_v2_contract,
)
from app.services.ga_readiness_service import get_ga_readiness


SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "015-ga-candidate"
    / "contracts"
    / "openapi-v2.generated.json"
)


def test_frozen_openapi_v2_contract_has_no_drift_or_v1_paths() -> None:
    from app.main import app

    rendered = render_openapi_v2_contract(app)
    assert SNAPSHOT.read_bytes() == rendered
    assert hashlib.sha256(rendered).hexdigest() == EXPECTED_CONTRACT_SHA256
    contract = json.loads(rendered)
    assert contract["info"]["version"] == CONTRACT_VERSION
    assert contract["paths"]
    assert all(path.startswith("/api/v2") for path in contract["paths"])
    assert "DemoDataCleanupOut" not in contract["components"].get("schemas", {})
    assert "/api/v1/demo-data" not in app.openapi()["paths"]
    assert "/api/v2/operations/ga-readiness" in build_openapi_v2_contract(app)["paths"]


@pytest.mark.asyncio
async def test_v1_lane_advertises_compatibility_without_sunset() -> None:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        v1 = await client.get("/api/v1/does-not-exist")
        v2 = await client.get("/api/v2/does-not-exist")

    assert v1.status_code == 404
    assert v1.headers["deprecation"] == "true"
    assert v1.headers["x-duckdock-api-compatibility"] == "v1-supported-through-2.x"
    assert v1.headers["link"] == '</api/v2>; rel="successor-version"'
    assert "sunset" not in v1.headers
    assert "deprecation" not in v2.headers


@pytest.mark.asyncio
async def test_ga_readiness_blocks_missing_domain_evidence(async_session) -> None:
    await async_session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
    await async_session.execute(
        text("INSERT INTO alembic_version(version_num) VALUES ('20260804_0062')")
    )

    async def prometheus_ready() -> tuple[bool, str]:
        return True, "HTTP 200 · test"

    result = await get_ga_readiness(
        async_session,
        openapi_contract_digest=EXPECTED_CONTRACT_SHA256,
        prometheus_probe=prometheus_ready,
    )

    assert result["status"] == "BLOCKED"
    checks = {item["key"]: item for item in result["checks"]}
    assert checks["database_revision"]["status"] == "PASS"
    assert checks["openapi_v2_contract"]["status"] == "PASS"
    assert checks["prometheus"]["status"] == "PASS"
    assert checks["runtime_inventory"]["status"] == "BLOCK"
    assert checks["verified_package"]["status"] == "BLOCK"
    assert checks["release_receipt"]["status"] == "BLOCK"
    assert checks["recovery_drill"]["status"] == "BLOCK"
    assert result["block_count"] >= 6
