from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from scripts.collect_ga_target_readiness import collect, parse_args


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"


def _arguments(base_url: str = "https://duckdock.example.com") -> list[str]:
    return [
        "--base-url",
        base_url,
        "--target-environment",
        "customer-production",
        "--source-commit",
        COMMIT,
        "--backend-image",
        BACKEND_IMAGE,
        "--frontend-image",
        FRONTEND_IMAGE,
    ]


@pytest.mark.asyncio
async def test_collector_fetches_readiness_without_retaining_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_token = "ga-admin-token-must-not-be-retained"
    monkeypatch.setenv("DUCKDOCK_GA_ADMIN_TOKEN", admin_token)
    checked_at = datetime.now(timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://duckdock.example.com/api/v2/operations/ga-readiness"
        assert request.headers["Authorization"] == f"Bearer {admin_token}"
        return httpx.Response(
            200,
            json={
                "profile_version": "duckdock-2-ga-readiness-v1",
                "contract_version": "2.0.0",
                "contract_digest": "d" * 64,
                "expected_db_revision": "20260804_0062",
                "current_db_revision": "20260804_0062",
                "status": "READY",
                "pass_count": 14,
                "warn_count": 0,
                "block_count": 0,
                "checked_at": checked_at,
                "checks": [{"key": f"check-{index}", "status": "PASS"} for index in range(14)],
            },
        )

    report = await collect(
        parse_args(_arguments()),
        transport=httpx.MockTransport(handler),
    )

    assert report["schema_version"] == "duckdock-ga-target-readiness-v1"
    assert report["scope"] == "target-production"
    assert report["target_environment"] == "customer-production"
    assert report["source_commit"] == COMMIT
    assert report["passed"] is True
    assert report["transport"] == "network HTTPS against target"
    assert admin_token not in json.dumps(report)


def test_collector_rejects_plain_http_for_production_scope() -> None:
    with pytest.raises(SystemExit):
        parse_args(_arguments("http://127.0.0.1:8801"))

    args = parse_args(
        [
            *_arguments("http://127.0.0.1:8801"),
            "--scope",
            "local-validation",
            "--allow-http-localhost",
        ]
    )
    assert args.release_binding["scope"] == "local-validation"


@pytest.mark.asyncio
async def test_collector_requires_admin_token_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DUCKDOCK_GA_ADMIN_TOKEN", raising=False)

    with pytest.raises(ValueError, match="credential environment variable"):
        await collect(parse_args(_arguments()), transport=httpx.MockTransport(lambda _request: None))
