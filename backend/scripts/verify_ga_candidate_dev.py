#!/usr/bin/env python3
"""Run the E09 GA candidate gate against live DuckDock and local Hermes."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.models.user import AuthSource, SystemRole, User


DEFAULT_USERNAME = "e09-ga-validator"
DEFAULT_PASSWORD = "DuckDock@E09Local2026!"
logger = logging.getLogger(__name__)


async def _ensure_admin(username: str, password: str) -> None:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                email=f"{username}@duckdock.dev",
                hashed_password=hash_password(password),
                system_role=SystemRole.ADMIN,
                auth_source=AuthSource.LOCAL,
                is_active=True,
            )
            db.add(user)
        else:
            user.email = f"{username}@duckdock.dev"
            user.hashed_password = hash_password(password)
            user.system_role = SystemRole.ADMIN
            user.auth_source = AuthSource.LOCAL
            user.is_active = True
        await db.commit()


class Api:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30)
        self.headers: dict[str, str] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected: set[int] | None = None,
    ) -> tuple[httpx.Response, Any]:
        response = await self.client.request(
            method,
            path,
            json=payload,
            params=params,
            headers=headers if headers is not None else self.headers,
        )
        if response.status_code not in (expected or {200, 201, 204}):
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text[:500]
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {detail}")
        body = None if response.status_code == 204 else response.json()
        return response, body

    async def login(self, username: str, password: str) -> None:
        _, body = await self.request(
            "POST",
            "/api/v1/auth/login",
            payload={"username": username, "password": password},
            headers={},
        )
        self.headers = {"Authorization": f"Bearer {body['access_token']}"}


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    await _ensure_admin(args.username, args.password)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    async with httpx.AsyncClient(timeout=10) as client:
        hermes_response = await client.get(args.hermes_models_url)
        hermes_response.raise_for_status()
        hermes_models = [
            item["id"]
            for item in hermes_response.json().get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
    if not hermes_models:
        raise RuntimeError("local Hermes returned no model identities")

    api = Api(args.duckdock_url)
    workload_public_id = ""
    try:
        await api.login(args.username, args.password)

        v1_response = await api.client.get("/api/v1/does-not-exist")
        if (
            v1_response.status_code != 404
            or v1_response.headers.get("deprecation") != "true"
            or v1_response.headers.get("x-duckdock-api-compatibility")
            != "v1-supported-through-2.x"
            or "sunset" in v1_response.headers
        ):
            raise RuntimeError("v1 compatibility headers do not match the GA policy")

        _, identity = await api.request(
            "POST",
            "/api/v2/identity/workload-identities",
            payload={
                "namespace_id": args.namespace_id,
                "runtime_id": args.runtime_id,
                "principal_kind": "DEVICE",
                "name": "E09 GA live SLO probe",
                "device_id": f"e09-ga-probe-{stamp}",
                "scopes": ["report.heartbeat"],
            },
        )
        workload_public_id = identity["public_id"]
        workload_token = identity["token"]
        heartbeat_headers = {"Authorization": f"Bearer {workload_token}"}
        heartbeat_payload = {
            "device_id": f"e09-ga-probe-{stamp}",
            "reporter_version": "e09-ga-live",
            "status": "ok",
            "capabilities_json": {"ga_slo_probe": True},
        }
        for _ in range(args.samples):
            await api.request(
                "POST",
                "/api/v1/reporters/heartbeat",
                payload=heartbeat_payload,
                headers=heartbeat_headers,
            )

        timeline_end = datetime.now(timezone.utc) + timedelta(minutes=1)
        timeline_start = timeline_end - timedelta(days=7)
        for _ in range(args.samples):
            await api.request(
                "GET",
                "/api/v2/agent-runs",
                params={
                    "namespace_id": args.namespace_id,
                    "started_after": timeline_start.isoformat(),
                    "started_before": timeline_end.isoformat(),
                    "limit": 1,
                },
            )

        _, decisions = await api.request(
            "GET",
            "/api/v2/release-policy-decisions",
            params={"namespace_id": args.namespace_id, "limit": 1},
        )
        if not decisions:
            raise RuntimeError("no existing Release Policy decision is available for the GA probe")
        source_decision = decisions[0]
        policy_payload = {
            "namespace_id": args.namespace_id,
            "policy_version_public_id": source_decision["policy_version_public_id"],
            "idempotency_key": f"e09-ga-policy-{stamp}",
        }
        for _ in range(args.samples):
            await api.request(
                "POST",
                f"/api/v2/release-candidates/{source_decision['candidate_public_id']}/policy-evaluations",
                payload=policy_payload,
            )

        await api.request(
            "POST",
            f"/api/v2/identity/workload-identities/{workload_public_id}/revoke",
            payload={"reason": "E09 GA live SLO probe completed"},
        )
        workload_public_id = ""

        _, slo = await api.request(
            "POST",
            "/api/v2/operations/slo-evaluations",
            payload={
                "idempotency_key": f"e09-ga-slo-{stamp}",
                "window_minutes": 15,
            },
        )
        _, reconciliation = await api.request(
            "GET", "/api/v2/reconciliation/health"
        )
        _, readiness = await api.request(
            "GET", "/api/v2/operations/ga-readiness"
        )

        result = {
            "ok": slo["status"] == "HEALTHY" and readiness["status"] == "READY",
            "hermes_models": sorted(hermes_models),
            "v1_compatibility": v1_response.headers[
                "x-duckdock-api-compatibility"
            ],
            "slo": {
                "public_id": slo["public_id"],
                "status": slo["status"],
                "request_count": slo["request_count"],
                "error_count": slo["error_count"],
                "evidence_ingest_p95_ms": slo["evidence_ingest_p95_ms"],
                "run_timeline_p95_ms": slo["run_timeline_p95_ms"],
                "policy_decision_p95_ms": slo["policy_decision_p95_ms"],
                "reason_codes": slo["reason_codes"],
            },
            "reconciliation": reconciliation,
            "ga_readiness": {
                "status": readiness["status"],
                "pass_count": readiness["pass_count"],
                "warn_count": readiness["warn_count"],
                "block_count": readiness["block_count"],
                "contract_version": readiness["contract_version"],
                "contract_digest": readiness["contract_digest"],
                "database_revision": readiness["current_db_revision"],
                "checks": {
                    item["key"]: item["status"] for item in readiness["checks"]
                },
            },
        }
        if not result["ok"]:
            raise RuntimeError(json.dumps(result, indent=2, sort_keys=True))
        return result
    finally:
        if workload_public_id:
            try:
                await api.request(
                    "POST",
                    f"/api/v2/identity/workload-identities/{workload_public_id}/revoke",
                    payload={"reason": "E09 GA probe cleanup after failure"},
                )
            except Exception:
                logger.exception(
                    "Failed to revoke temporary GA validation workload identity"
                )
        await api.close()
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdock-url", default="http://127.0.0.1:8801")
    parser.add_argument("--hermes-models-url", default="http://127.0.0.1:50070/v1/models")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--namespace-id", type=int, default=6)
    parser.add_argument("--runtime-id", type=int, default=9)
    parser.add_argument("--samples", type=int, default=25)
    return parser.parse_args()


def main() -> int:
    result = asyncio.run(verify(parse_args()))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
