#!/usr/bin/env python3
"""Verify E07 Identity/Security against live DuckDock, MySQL and local Hermes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.models.control_plane import AIAsset, AssetOwnership, OwnerType
from app.models.iam import (
    EmploymentStatus,
    IdentityLink,
    SSOProviderConfig,
    SSOProviderType,
    UserHandoverProfile,
)
from app.models.namespace import NamespaceMember, NamespaceRole
from app.models.user import AuthSource, SystemRole, User


DEFAULT_ADMIN = "e07-identity-validator"
DEFAULT_SUBJECT = "e07-directory-subject"
DEFAULT_PASSWORD = "DuckDock@E07Local2026!"
DEFAULT_NAMESPACE_ID = 6
DEFAULT_RUNTIME_ID = 9
DEFAULT_ASSET_ID = 125
PROVIDER_NAME = "e07-staging-scim-directory"
EXTERNAL_SUBJECT = "duckdock-e07-subject"
ROTATED_KEY_ID = "duckdock-e07-rotation-20260804"


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
        expected: set[int] | None = None,
    ) -> tuple[int, Any]:
        response = await self.client.request(
            method,
            path,
            json=payload,
            params=params,
            headers=headers if headers is not None else self.headers,
        )
        allowed = expected or {200, 201, 204}
        if response.status_code not in allowed:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text[:500]
            raise RuntimeError(
                f"{method} {path} -> {response.status_code}: {detail}"
            )
        body = None if response.status_code == 204 else response.json()
        return response.status_code, body

    async def login(self, username: str, password: str) -> None:
        _, body = await self.request(
            "POST",
            "/api/v1/auth/login",
            payload={"username": username, "password": password},
            headers={},
        )
        self.headers = {"Authorization": f"Bearer {body['access_token']}"}


async def _seed_directory_prerequisites(args: argparse.Namespace) -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.username == args.admin_username))
        if admin is None:
            admin = User(
                username=args.admin_username,
                email=f"{args.admin_username}@duckdock.dev",
                hashed_password=hash_password(args.password),
                system_role=SystemRole.ADMIN,
                auth_source=AuthSource.LOCAL,
                is_active=True,
            )
            db.add(admin)
            await db.flush()
        else:
            admin.hashed_password = hash_password(args.password)
            admin.system_role = SystemRole.ADMIN
            admin.is_active = True

        subject = await db.scalar(select(User).where(User.username == args.subject_username))
        if subject is None:
            subject = User(
                username=args.subject_username,
                email=f"{args.subject_username}@duckdock.dev",
                hashed_password=hash_password(args.password),
                system_role=SystemRole.USER,
                auth_source=AuthSource.OIDC,
                enterprise_uid="e07-staging-employee",
                is_active=True,
            )
            db.add(subject)
            await db.flush()
        else:
            subject.hashed_password = hash_password(args.password)
            subject.system_role = SystemRole.USER
            subject.auth_source = AuthSource.OIDC
            subject.is_active = True

        provider = await db.scalar(
            select(SSOProviderConfig).where(SSOProviderConfig.name == PROVIDER_NAME)
        )
        if provider is None:
            provider = SSOProviderConfig(
                provider_type=SSOProviderType.OIDC,
                name=PROVIDER_NAME,
                enabled=True,
                issuer_url="https://staging-directory.duckdock.dev",
            )
            db.add(provider)
            await db.flush()
        else:
            provider.enabled = True

        link = await db.scalar(
            select(IdentityLink).where(
                IdentityLink.provider_id == provider.id,
                IdentityLink.external_subject == EXTERNAL_SUBJECT,
            )
        )
        if link is None:
            link = IdentityLink(
                user_id=subject.id,
                provider_id=provider.id,
                source=AuthSource.OIDC,
                issuer=provider.issuer_url,
                external_subject=EXTERNAL_SUBJECT,
                external_uid="e07-staging-employee",
                username=subject.username,
                email=subject.email,
                is_active=True,
            )
            db.add(link)
        else:
            link.user_id = subject.id
            link.is_active = True
            link.disabled_at = None

        membership = await db.scalar(
            select(NamespaceMember).where(
                NamespaceMember.namespace_id == args.namespace_id,
                NamespaceMember.user_id == subject.id,
            )
        )
        if membership is None:
            db.add(
                NamespaceMember(
                    namespace_id=args.namespace_id,
                    user_id=subject.id,
                    role=NamespaceRole.ADMIN,
                )
            )
        else:
            membership.role = NamespaceRole.ADMIN

        profile = await db.scalar(
            select(UserHandoverProfile).where(
                UserHandoverProfile.user_id == subject.id
            )
        )
        if profile is None:
            profile = UserHandoverProfile(user_id=subject.id)
            db.add(profile)
        profile.manager_user_id = admin.id
        profile.handover_receiver_user_id = admin.id
        profile.employment_status = EmploymentStatus.ACTIVE

        asset = await db.scalar(
            select(AIAsset).where(
                AIAsset.id == args.asset_id,
                AIAsset.namespace_id == args.namespace_id,
            )
        )
        if asset is None:
            raise RuntimeError("E07 validation asset was not found in the Namespace")
        ownership = await db.scalar(
            select(AssetOwnership).where(
                AssetOwnership.asset_id == asset.id,
                AssetOwnership.user_id == subject.id,
            )
        )
        if ownership is None:
            db.add(
                AssetOwnership(
                    namespace_id=args.namespace_id,
                    asset_id=asset.id,
                    owner_type=OwnerType.MAINTAINER,
                    user_id=subject.id,
                    is_primary=False,
                )
            )
        await db.commit()
        return provider.id, subject.id


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    provider_id, subject_user_id = await _seed_directory_prerequisites(args)
    async with httpx.AsyncClient(timeout=30) as client:
        hermes = (await client.get(args.hermes_models_url)).json()
    model_ids = sorted(row["id"] for row in hermes.get("data", []))
    if not model_ids:
        raise RuntimeError("local Hermes returned no models")

    admin_api = Api(args.duckdock_url)
    subject_api = Api(args.duckdock_url)
    try:
        await admin_api.login(args.admin_username, args.password)
        await subject_api.login(args.subject_username, args.password)
        _, identity = await subject_api.request(
            "POST",
            "/api/v2/identity/workload-identities",
            payload={
                "namespace_id": args.namespace_id,
                "runtime_id": args.runtime_id,
                "principal_kind": "DEVICE",
                "name": "E07 directory revocation probe",
                "device_id": "e07-staging-device",
                "scopes": ["execution.write", "report.heartbeat"],
            },
        )
        workload_token = identity.pop("token")
        heartbeat_payload = {
            "device_id": "e07-staging-device",
            "reporter_version": "e07-live",
            "status": "ok",
            "capabilities_json": {"identity_lifecycle": True},
        }
        before_heartbeat, _ = await admin_api.request(
            "POST",
            "/api/v1/reporters/heartbeat",
            payload=heartbeat_payload,
            headers={"Authorization": f"Bearer {workload_token}"},
        )
        _, directory_credential = await admin_api.request(
            "POST",
            "/api/v2/identity/directory-credentials",
            payload={
                "provider_id": provider_id,
                "name": "E07 staging SCIM verifier",
            },
        )
        scim_token = directory_credential.pop("token")
        event_id = f"e07-disable-{identity['public_id']}"
        scim_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "active", "value": False}
            ],
        }
        scim_headers = {
            "Authorization": f"Bearer {scim_token}",
            "X-DuckDock-Event-Id": event_id,
        }
        _, lifecycle = await admin_api.request(
            "PATCH",
            f"/api/v2/scim/v2/Users/{EXTERNAL_SUBJECT}",
            payload=scim_payload,
            headers=scim_headers,
        )
        _, replay = await admin_api.request(
            "PATCH",
            f"/api/v2/scim/v2/Users/{EXTERNAL_SUBJECT}",
            payload=scim_payload,
            headers=scim_headers,
        )
        if replay["duckdock_event"]["public_id"] != lifecycle["duckdock_event"]["public_id"]:
            raise RuntimeError("SCIM retry did not return the original lifecycle event")

        after_user_status, _ = await subject_api.request(
            "GET", "/api/v1/auth/me", expected={401}
        )
        after_heartbeat, _ = await admin_api.request(
            "POST",
            "/api/v1/reporters/heartbeat",
            payload=heartbeat_payload,
            headers={"Authorization": f"Bearer {workload_token}"},
            expected={401},
        )
        event = lifecycle["duckdock_event"]
        if event["credentials_revoked"] < 1 or not event["handover_case_ids_json"]:
            raise RuntimeError("directory disable did not revoke credential and trigger handover")
        case_id = event["handover_case_ids_json"][0]
        _, handover = await admin_api.request("GET", f"/api/v1/handovers/{case_id}")
        if handover["subject_user_id"] != subject_user_id:
            raise RuntimeError("auto-created Handover subject does not match directory user")

        _, signing_keys = await admin_api.request(
            "GET",
            "/api/v2/package-signing-keys",
            params={"namespace_id": args.namespace_id},
        )
        active_e07 = next(
            (
                row
                for row in signing_keys
                if row["key_id"] == ROTATED_KEY_ID and row["status"] == "ACTIVE"
            ),
            None,
        )
        if active_e07 is None:
            predecessor = next(
                (row for row in signing_keys if row["status"] == "ACTIVE"), None
            )
            if predecessor is None:
                raise RuntimeError("no active Package signing key is available for rotation")
            seed = hashlib.sha256(b"duckdock:e07:dev-signing-key").digest()
            private_key = Ed25519PrivateKey.from_private_bytes(seed)
            public_pem = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")
            _, active_e07 = await admin_api.request(
                "POST",
                f"/api/v2/package-signing-keys/{predecessor['public_id']}/rotate",
                payload={
                    "key_id": ROTATED_KEY_ID,
                    "algorithm": "ED25519",
                    "public_key_pem": public_pem,
                },
            )

        return {
            "ok": True,
            "namespace_id": args.namespace_id,
            "runtime_id": args.runtime_id,
            "hermes_model_count": len(model_ids),
            "workload_identity_public_id": identity["public_id"],
            "workload_identity_kind": identity["principal_kind"],
            "heartbeat_before_disable_status": before_heartbeat,
            "user_request_after_disable_status": after_user_status,
            "heartbeat_after_disable_status": after_heartbeat,
            "directory_event_public_id": event["public_id"],
            "directory_event_action": event["action"],
            "credentials_revoked": event["credentials_revoked"],
            "memberships_removed": event["memberships_removed"],
            "handover_case_id": case_id,
            "handover_receiver_user_id": handover["receiver_user_id"],
            "signing_key_public_id": active_e07["public_id"],
            "signing_key_rotation_sequence": active_e07["rotation_sequence"],
            "signing_key_fingerprint": active_e07["public_key_fingerprint"],
            "scim_replay_idempotent": True,
        }
    finally:
        await admin_api.close()
        await subject_api.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdock-url", default="http://127.0.0.1:8801")
    parser.add_argument(
        "--hermes-models-url", default="http://127.0.0.1:50070/v1/models"
    )
    parser.add_argument("--namespace-id", type=int, default=DEFAULT_NAMESPACE_ID)
    parser.add_argument("--runtime-id", type=int, default=DEFAULT_RUNTIME_ID)
    parser.add_argument("--asset-id", type=int, default=DEFAULT_ASSET_ID)
    parser.add_argument("--admin-username", default=DEFAULT_ADMIN)
    parser.add_argument("--subject-username", default=DEFAULT_SUBJECT)
    parser.add_argument(
        "--password", default=os.getenv("E07_VALIDATOR_PASSWORD", DEFAULT_PASSWORD)
    )
    return parser.parse_args()


async def _main() -> None:
    try:
        print(json.dumps(await verify(parse_args()), ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
