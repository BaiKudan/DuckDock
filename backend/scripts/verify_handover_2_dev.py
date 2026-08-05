#!/usr/bin/env python3
"""Verify Handover 2.0 against the live DuckDock dev stack and local Hermes.

All Handover, Deployment, evidence, acceptance, signing and download operations
use live HTTP APIs. The database is touched only to ensure the dedicated local
validator identity exists and has a Namespace membership.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import zipfile
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.models.namespace import NamespaceMember, NamespaceRole
from app.models.package_registry import AgentPackageVersion
from app.models.user import SystemRole, User


DEFAULT_PACKAGE_VERSION = "pkgv_35c6af5165f54f69aad2c6d8acb59175"
DEFAULT_USERNAME = "e06-handover-validator"
DEFAULT_PASSWORD = "DuckDock@E06Local2026!"
DEFAULT_EMAIL_DOMAIN = "duckdock.dev"
SIGNING_KEY_ID = "duckdock-e06-dev-handover"


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
        payload: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self.client.request(
            method,
            path,
            json=payload,
            params=params,
            headers=headers or self.headers,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text[:500]
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {detail}")
        if response.status_code == 204:
            return None
        return response.json()

    async def login(self, username: str, password: str) -> None:
        body = await self.request(
            "POST",
            "/api/v1/auth/login",
            payload={"username": username, "password": password},
            headers={},
        )
        self.headers = {"Authorization": f"Bearer {body['access_token']}"}


async def _package_subject(public_id: str) -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        version = await db.scalar(
            select(AgentPackageVersion)
            .options(selectinload(AgentPackageVersion.package))
            .where(AgentPackageVersion.public_id == public_id)
        )
        if version is None or version.package.agent_asset_id is None:
            raise RuntimeError("validated PackageVersion or its Agent asset was not found")
        return version.namespace_id, version.package.agent_asset_id


async def _ensure_validator(namespace_id: int, username: str, password: str) -> None:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                email=f"{username}@{DEFAULT_EMAIL_DOMAIN}",
                hashed_password=hash_password(password),
                system_role=SystemRole.ADMIN,
                is_active=True,
            )
            db.add(user)
            await db.flush()
        else:
            user.email = f"{username}@{DEFAULT_EMAIL_DOMAIN}"
            user.hashed_password = hash_password(password)
            user.system_role = SystemRole.ADMIN
            user.is_active = True
        membership = await db.scalar(
            select(NamespaceMember).where(
                NamespaceMember.namespace_id == namespace_id,
                NamespaceMember.user_id == user.id,
            )
        )
        if membership is None:
            db.add(
                NamespaceMember(
                    namespace_id=namespace_id,
                    user_id=user.id,
                    role=NamespaceRole.ADMIN,
                )
            )
        else:
            membership.role = NamespaceRole.ADMIN
        await db.commit()


async def _hermes_snapshot(url: str) -> tuple[list[str], str]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        document = response.json()
    rows = document.get("data") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Hermes did not return an OpenAI-compatible model list")
    model_ids = sorted(
        row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
    )
    if not model_ids:
        raise RuntimeError("Hermes returned no models")
    digest = hashlib.sha256(
        json.dumps(
            {"model_ids": model_ids, "capture": "metadata_only", "lane": "handover-2"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return model_ids, digest


def _private_key(identity: str) -> Ed25519PrivateKey:
    seed = hashlib.sha256(
        f"duckdock-e06-local-dev-handover-signing-key:{identity}".encode()
    ).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    namespace_id, agent_asset_id = await _package_subject(args.package_version_public_id)
    await _ensure_validator(namespace_id, args.username, args.password)
    model_ids, configuration_digest = await _hermes_snapshot(args.hermes_models_url)
    api = Api(args.duckdock_url)
    try:
        await api.login(args.username, args.password)
        me = await api.request("GET", "/api/v1/auth/me")
        package_version = await api.request(
            "GET", f"/api/v2/agent-package-versions/{args.package_version_public_id}"
        )
        asset = await api.request("GET", f"/api/v1/assets/{agent_asset_id}")
        metadata = dict(asset.get("metadata_json") or {})
        metadata.update(
            {
                "runbook_url": "https://runbooks.duckdock.dev/hermes-production",
                "runbook_version": "e06-20260804",
            }
        )
        await api.request(
            "PATCH",
            f"/api/v1/assets/{agent_asset_id}",
            payload={"metadata_json": metadata},
        )
        ownerships = await api.request(
            "GET", f"/api/v1/assets/{agent_asset_id}/ownership"
        )
        if not any(row.get("user_id") == me["id"] for row in ownerships):
            await api.request(
                "POST",
                f"/api/v1/assets/{agent_asset_id}/ownership",
                payload={
                    "owner_type": "business_owner",
                    "user_id": me["id"],
                    "namespace_id": namespace_id,
                    "confidence": 1,
                    "is_primary": True,
                },
            )

        deployments = await api.request(
            "GET",
            "/api/v1/deployments",
            params={"namespace_id": namespace_id, "environment": "production"},
        )
        revision = f"hermes-e06-production-{configuration_digest[:12]}"
        deployment = next(
            (
                row
                for row in deployments
                if row["revision"] == revision
                and row["runtime_id"] == args.runtime_id
                and row["external_deployment_id"]
                == "duckdock-e06-hermes-production"
            ),
            None,
        )
        if deployment is None:
            deployment = await api.request(
                "POST",
                "/api/v1/deployments",
                payload={
                    "namespace_id": namespace_id,
                    "runtime_id": args.runtime_id,
                    "agent_asset_id": agent_asset_id,
                    "package_version_public_id": args.package_version_public_id,
                    "external_deployment_id": "duckdock-e06-hermes-production",
                    "environment": "production",
                    "revision": revision,
                    "configuration_digest": configuration_digest,
                    "components": [
                        {
                            "component_key": "agent",
                            "component_role": "agent",
                            "ai_asset_id": agent_asset_id,
                            "content_digest": package_version["manifest_digest"],
                            "configuration": {
                                "provider": "custom",
                                "endpoint_kind": "openai-compatible",
                            },
                        }
                    ],
                },
            )
        if deployment["status"] == "REGISTERED":
            deployment = await api.request(
                "POST", f"/api/v1/deployments/{deployment['public_id']}/activate"
            )
        if deployment["status"] != "ACTIVE":
            raise RuntimeError(f"production Deployment is {deployment['status']}")

        title = (
            "E06 real Hermes evidence-driven handover "
            f"runtime-{args.runtime_id}"
        )
        cases = await api.request("GET", "/api/v1/handovers")
        case = next((row for row in cases if row["title"] == title), None)
        if case is None:
            case = await api.request(
                "POST",
                "/api/v1/handovers",
                payload={
                    "namespace_id": namespace_id,
                    "case_type": "employee_offboarding",
                    "title": title,
                    "subject_user_id": me["id"],
                    "receiver_user_id": me["id"],
                    "fallback_owner_user_id": me["id"],
                    "runtime_ids": [args.runtime_id],
                    "collection_scope": {
                        "users": [me["id"]],
                        "include_work_traces": True,
                        "include_artifacts": True,
                        "lookback_days": 30,
                    },
                    "metadata_json": {"validation_lane": "E06-real-hermes"},
                },
            )
        if case["status"] in {"draft", "collecting", "analyzing"}:
            await api.request("POST", f"/api/v1/handovers/{case['id']}/analyze")
            case = await api.request("GET", f"/api/v1/handovers/{case['id']}")
        items = await api.request("GET", f"/api/v1/handovers/{case['id']}/items")
        if not items:
            raise RuntimeError("Handover analysis produced no item")
        item = next((row for row in items if row["asset_id"] == agent_asset_id), items[0])
        if item.get("evidence_id") is None:
            raise RuntimeError("real handover item lacks analysis evidence")
        approvals = await api.request(
            "GET", f"/api/v1/handovers/{case['id']}/approvals"
        )
        if case["status"] == "pending_approval" and not approvals:
            approvals = await api.request(
                "POST",
                f"/api/v1/handovers/{case['id']}/submit",
                payload=[{"approval_type": "receiver", "approver_user_id": me["id"]}],
            )
        for approval in approvals:
            if approval["status"] == "pending":
                await api.request(
                    "POST",
                    f"/api/v1/handovers/{case['id']}/approvals/{approval['id']}/decide",
                    payload={"decision": "approved", "comment": "E06 real validation"},
                )
        case = await api.request("GET", f"/api/v1/handovers/{case['id']}")
        if case["status"] == "approved":
            await api.request(
                "POST",
                f"/api/v1/handovers/{case['id']}/execute",
                payload={
                    "execution_mode": "manual",
                    "idempotency_key": f"e06-execute-{case['id']}",
                },
            )
        actions = await api.request("GET", f"/api/v1/handovers/{case['id']}/actions")
        for action in actions:
            if action["status"] in {"pending", "running", "requires_manual"}:
                await api.request(
                    "POST",
                    f"/api/v1/handovers/{case['id']}/actions/{action['id']}/complete",
                    payload={
                        "result": "succeeded",
                        "note": "Hermes production ownership transfer confirmed",
                        "evidence_ids": [item["evidence_id"]],
                        "idempotency_key": f"e06-receipt-{action['id']}",
                    },
                )
        case = await api.request("GET", f"/api/v1/handovers/{case['id']}")
        if case["status"] not in {"verifying", "completed"}:
            raise RuntimeError(f"Handover did not reach verifying: {case['status']}")

        snapshots = await api.request(
            "GET",
            "/api/v2/handover-evidence-snapshots",
            params={"namespace_id": namespace_id, "handover_case_id": case["id"]},
        )
        snapshot = next(
            (row for row in snapshots if row["subject"].get("receiver_user_id") == me["id"]),
            None,
        )
        if snapshot is None:
            snapshot = await api.request(
                "POST",
                "/api/v2/handover-evidence-snapshots",
                payload={
                    "handover_case_id": case["id"],
                    "idempotency_key": f"e06-snapshot-{case['id']}-{revision}",
                },
            )
        initial_readiness = snapshot["readiness_outcome"]
        blocking_types = sorted(
            {
                obligation["obligation_type"]
                for obligation in snapshot["obligations"]
                if obligation["severity"] == "BLOCKING"
            }
        )
        for obligation in snapshot["obligations"]:
            if obligation["receipt"] is not None:
                continue
            if obligation["severity"] != "BLOCKING":
                continue
            await api.request(
                "POST",
                f"/api/v2/handover-obligations/{obligation['public_id']}/receipts",
                payload={
                    "decision": "FULFILLED",
                    "note": f"Reviewed and resolved {obligation['obligation_key']}",
                    "evidence_ids": [item["evidence_id"]] if obligation["requires_evidence"] else [],
                    "idempotency_key": f"e06-obligation-{obligation['public_id']}",
                },
            )
        snapshot = await api.request(
            "GET", f"/api/v2/handover-evidence-snapshots/{snapshot['public_id']}"
        )
        if snapshot["current_readiness_outcome"] != "READY":
            raise RuntimeError("resolved Handover snapshot did not become READY")

        acceptances = await api.request(
            "GET",
            "/api/v2/handover-acceptances",
            params={"namespace_id": namespace_id, "handover_case_id": case["id"]},
        )
        acceptance = next((row for row in acceptances if row["decision"] == "ACCEPTED"), None)
        if acceptance is None:
            acceptance = await api.request(
                "POST",
                "/api/v2/handover-acceptances",
                payload={
                    "snapshot_public_id": snapshot["public_id"],
                    "decision": "ACCEPTED",
                    "comment": "Receiver accepted the exact Hermes evidence snapshot",
                    "acknowledges_failures": False,
                    "idempotency_key": f"e06-accept-{snapshot['public_id']}",
                },
            )

        private_key = _private_key(
            f"runtime-{args.runtime_id}:{acceptance['public_id']}"
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        payload = await api.request(
            "GET", f"/api/v2/handover-acceptances/{acceptance['public_id']}/signing-payload"
        )
        manifest_bytes = base64.b64decode(payload["payload_base64"])
        packages = await api.request(
            "GET",
            "/api/v2/handover-signed-packages",
            params={"namespace_id": namespace_id, "handover_case_id": case["id"]},
        )
        package = next(
            (row for row in packages if row["acceptance_public_id"] == acceptance["public_id"]),
            None,
        )
        if package is None:
            keys = await api.request(
                "GET",
                "/api/v2/package-signing-keys",
                params={"namespace_id": namespace_id},
            )
            key_id = f"{SIGNING_KEY_ID}-runtime-{args.runtime_id}"
            signing_key = next(
                (
                    row
                    for row in keys
                    if row["key_id"] == key_id and row["status"] == "ACTIVE"
                ),
                None,
            )
            if signing_key is None:
                if any(row["key_id"] == key_id for row in keys):
                    key_id = f"{key_id}-{acceptance['public_id'][-8:]}"
                    signing_key = next(
                        (
                            row
                            for row in keys
                            if row["key_id"] == key_id
                            and row["status"] == "ACTIVE"
                        ),
                        None,
                    )
                if signing_key is None:
                    signing_key = await api.request(
                        "POST",
                        "/api/v2/package-signing-keys",
                        payload={
                            "namespace_id": namespace_id,
                            "key_id": key_id,
                            "algorithm": "ED25519",
                            "public_key_pem": public_pem,
                        },
                    )
            signature = base64.b64encode(private_key.sign(manifest_bytes)).decode(
                "ascii"
            )
            package = await api.request(
                "POST",
                "/api/v2/handover-signed-packages",
                payload={
                    "acceptance_public_id": acceptance["public_id"],
                    "signing_key_public_id": signing_key["public_id"],
                    "signature": signature,
                    "idempotency_key": f"e06-package-{acceptance['public_id']}",
                },
            )
        download = await api.request(
            "POST",
            f"/api/v1/evidence/{package['evidence_item_id']}/download-link",
            payload={"reason": "E06 signed package independent verification"},
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(download["download_url"])
            response.raise_for_status()
            archive = response.content
        archive_digest = hashlib.sha256(archive).hexdigest()
        if archive_digest != package["archive_digest"]:
            raise RuntimeError("MinIO package readback digest mismatch")
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            archived_manifest = bundle.read("manifest.json")
            signature_document = json.loads(bundle.read("signature.json"))
        if archived_manifest != manifest_bytes:
            raise RuntimeError("signed package manifest differs from signing payload")
        private_key.public_key().verify(
            base64.b64decode(signature_document["signature"]),
            archived_manifest,
        )
        return {
            "ok": True,
            "namespace_id": namespace_id,
            "runtime_id": args.runtime_id,
            "hermes_model_ids": model_ids,
            "production_deployment_public_id": deployment["public_id"],
            "production_revision": deployment["revision"],
            "handover_case_id": case["id"],
            "snapshot_public_id": snapshot["public_id"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "initial_readiness": initial_readiness,
            "blocking_obligation_types": blocking_types,
            "current_readiness": snapshot["current_readiness_outcome"],
            "acceptance_public_id": acceptance["public_id"],
            "signed_package_public_id": package["public_id"],
            "manifest_digest": package["manifest_digest"],
            "archive_digest": package["archive_digest"],
            "signing_key_fingerprint": package["signing_key_fingerprint"],
            "minio_readback_bytes": len(archive),
            "signature_verified": True,
        }
    finally:
        await api.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdock-url", default="http://127.0.0.1:8801")
    parser.add_argument("--hermes-models-url", default="http://127.0.0.1:50070/v1/models")
    parser.add_argument("--package-version-public-id", default=DEFAULT_PACKAGE_VERSION)
    parser.add_argument("--runtime-id", type=int, default=9)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument(
        "--password",
        default=os.getenv("E06_VALIDATOR_PASSWORD", DEFAULT_PASSWORD),
    )
    return parser.parse_args()


async def _main() -> None:
    try:
        result = await verify(parse_args())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
