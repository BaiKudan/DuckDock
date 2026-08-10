#!/usr/bin/env python3
"""Run a real local E05 Release Control verification against Hermes.

The script uses the live DuckDock HTTP APIs for every release operation. It
touches the database only to ensure a dedicated local validator login and to
bootstrap a short-lived execution.write credential for synthetic metadata-only
canary evidence. Release receipt credentials are issued by the product API;
all validation credentials are held in memory and immediately revoked.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.api_token_security import hash_reporter_token_secret
from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.models.control_plane import ReporterCredential
from app.models.namespace import NamespaceMember, NamespaceRole
from app.models.package_registry import AgentPackageVersion
from app.models.user import SystemRole, User
from app.services.report_upload_service import generate_runtime_report_token
from app.services.reporter_identity_service import EXECUTION_WRITE_SCOPE


DEFAULT_PACKAGE_VERSION = "pkgv_35c6af5165f54f69aad2c6d8acb59175"
DEFAULT_USERNAME = "e05-release-validator"
DEFAULT_PASSWORD = "DuckDock@E05Local2026!"
DEFAULT_EMAIL_DOMAIN = "duckdock.dev"


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def _ensure_validator(
    *,
    namespace_id: int,
    username: str,
    password: str,
) -> None:
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


async def _package_subject(public_id: str) -> tuple[int, int, int]:
    async with AsyncSessionLocal() as db:
        version = await db.scalar(
            select(AgentPackageVersion)
            .options(selectinload(AgentPackageVersion.package))
            .where(AgentPackageVersion.public_id == public_id)
        )
        if version is None:
            raise RuntimeError(f"PackageVersion {public_id} was not found")
        package = version.package
        if package.agent_asset_id is None:
            raise RuntimeError("PackageVersion has no Agent asset")
        return version.namespace_id, package.agent_asset_id, version.id


async def _issue_execution_credential(
    *,
    runtime_id: int,
    username: str,
) -> tuple[int, str]:
    prefix, secret, token = generate_runtime_report_token()
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            raise RuntimeError("validator user disappeared before canary evidence bootstrap")
        row = ReporterCredential(
            runtime_id=runtime_id,
            user_id=user.id,
            device_id=f"hermes-e05-canary-{runtime_id}",
            name="Hermes E05 ephemeral canary writer",
            token_prefix=prefix,
            token_hash=hash_reporter_token_secret(secret),
            scopes=[EXECUTION_WRITE_SCOPE],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            metadata_json={"purpose": "e05_canary_validation"},
        )
        db.add(row)
        await db.flush()
        credential_id = row.id
        await db.commit()
    return credential_id, token


async def _revoke_execution_credential(
    *,
    credential_id: int,
    username: str,
) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.scalar(
            select(ReporterCredential).where(ReporterCredential.id == credential_id)
        )
        user = await db.scalar(select(User).where(User.username == username))
        if row is None:
            return
        row.is_active = False
        row.revoked_at = datetime.now(timezone.utc)
        row.revoked_by = user.id if user is not None else None
        row.revoked_reason = "one_shot_e05_canary_validation_complete"
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
        payload = await self.request(
            "POST",
            "/api/v1/auth/login",
            payload={"username": username, "password": password},
            headers={},
        )
        self.headers = {"Authorization": f"Bearer {payload['access_token']}"}


async def _hermes_snapshot(url: str) -> tuple[list[str], str]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        document = response.json()
    rows = document.get("data") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Hermes /v1/models did not return an OpenAI-compatible data array")
    model_ids = sorted(
        row["id"]
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    )
    if not model_ids:
        raise RuntimeError("Hermes returned no model identities")
    snapshot = {
        "endpoint_kind": "openai-compatible-model-list",
        "model_ids": model_ids,
        "content_capture": "metadata_only",
    }
    return model_ids, _canonical_digest(snapshot)


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    namespace_id, agent_asset_id, _package_version_id = await _package_subject(
        args.package_version_public_id
    )
    await _ensure_validator(
        namespace_id=namespace_id,
        username=args.username,
        password=args.password,
    )
    model_ids, configuration_digest = await _hermes_snapshot(args.hermes_models_url)
    deployment_scope = _canonical_digest(
        {
            "external_deployment_id": args.external_deployment_id,
            "environment": args.environment_name,
        }
    )[:8]
    revision_base = (
        f"hermes-e05-runtime-{args.runtime_id}-{deployment_scope}-"
        f"{configuration_digest[:12]}"
    )

    api = Api(args.duckdock_url)
    try:
        await api.login(args.username, args.password)
        package_version = await api.request(
            "GET",
            f"/api/v2/agent-package-versions/{args.package_version_public_id}",
        )
        package = await api.request(
            "GET",
            f"/api/v2/agent-packages/{package_version['package_public_id']}",
        )
        if package["agent_asset_id"] != agent_asset_id:
            raise RuntimeError("Package API and database disagree on Agent asset identity")

        deployments = await api.request(
            "GET",
            "/api/v1/deployments",
            params={"namespace_id": namespace_id},
        )
        runtime_id = args.runtime_id
        runtime_deployments = [row for row in deployments if row["runtime_id"] == runtime_id]
        if not runtime_deployments:
            raise RuntimeError(f"Runtime #{runtime_id} has no Deployment inventory evidence")
        revision = revision_base
        existing = next(
            (
                row
                for row in deployments
                if row["external_deployment_id"] == args.external_deployment_id
                and row["revision"] == revision
                and row["runtime_id"] == runtime_id
            ),
            None,
        )
        if existing is not None and existing["status"] in {"FAILED", "RETIRED"}:
            revision = f"{revision_base}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
            existing = None
        if existing is None:
            deployment = await api.request(
                "POST",
                "/api/v1/deployments",
                payload={
                    "namespace_id": namespace_id,
                    "runtime_id": runtime_id,
                    "agent_asset_id": agent_asset_id,
                    "package_version_public_id": args.package_version_public_id,
                    "external_deployment_id": args.external_deployment_id,
                    "environment": args.environment_name,
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
        else:
            deployment = existing

        environments = await api.request(
            "GET",
            "/api/v2/release-environments",
            params={"namespace_id": namespace_id},
        )
        environment = next(
            (row for row in environments if row["name"] == args.environment_name),
            None,
        )
        if environment is None:
            used_orders = {row["promotion_order"] for row in environments}
            order = next(value for value in range(0, 101) if value not in used_orders)
            environment = await api.request(
                "POST",
                "/api/v2/release-environments",
                payload={
                    "namespace_id": namespace_id,
                    "name": args.environment_name,
                    "kind": "DEVELOPMENT",
                    "promotion_order": order,
                    "protected": False,
                    "minimum_approvals": 0,
                    "requires_canary": False,
                },
            )

        candidates = await api.request(
            "GET",
            "/api/v2/release-candidates",
            params={"namespace_id": namespace_id},
        )
        candidate = next(
            (row for row in candidates if row["deployment_public_id"] == deployment["public_id"]),
            None,
        )
        if candidate is None:
            if deployment["status"] != "REGISTERED":
                raise RuntimeError(
                    f"existing Deployment is {deployment['status']} but has no ReleaseCandidate"
                )
            candidate = await api.request(
                "POST",
                "/api/v2/release-candidates",
                payload={
                    "namespace_id": namespace_id,
                    "package_version_public_id": args.package_version_public_id,
                    "deployment_public_id": deployment["public_id"],
                    "target_environment_public_id": environment["public_id"],
                    "baseline_candidate_public_id": None,
                    "idempotency_key": f"e05-candidate-{revision}",
                },
            )

        policies = await api.request(
            "GET",
            "/api/v2/release-policies",
            params={"namespace_id": namespace_id},
        )
        policy = next((row for row in policies if row["name"] == args.policy_name), None)
        if policy is None:
            policy = await api.request(
                "POST",
                "/api/v2/release-policies",
                payload={
                    "namespace_id": namespace_id,
                    "name": args.policy_name,
                    "description": "Real local Hermes E05 verification policy",
                },
            )
        policy_version = next(
            (
                row
                for row in policy.get("versions", [])
                if row["target_environment_public_id"] == environment["public_id"]
                and row["mode"] == "ENFORCE"
            ),
            None,
        )
        if policy_version is None:
            policy_version = await api.request(
                "POST",
                f"/api/v2/release-policies/{policy['public_id']}/versions",
                payload={
                    "namespace_id": namespace_id,
                    "target_environment_public_id": environment["public_id"],
                    "mode": "ENFORCE",
                    "rules": [
                        {
                            "rule_id": "package-evidence",
                            "rule_type": "PACKAGE_EVIDENCE_VERIFIED",
                        },
                        {
                            "rule_id": "signing-key",
                            "rule_type": "SIGNING_KEY_ACTIVE",
                        },
                        {
                            "rule_id": "vulnerability-threshold",
                            "rule_type": "MAX_VULNERABILITY_SEVERITY",
                            "maximum_severity": "HIGH",
                        },
                        {
                            "rule_id": "rollback-target",
                            "rule_type": "ROLLBACK_TARGET_REQUIRED",
                            "required": False,
                        },
                    ],
                },
            )

        decisions = await api.request(
            "GET",
            "/api/v2/release-policy-decisions",
            params={
                "namespace_id": namespace_id,
                "candidate_public_id": candidate["public_id"],
            },
        )
        decision = next(
            (
                row
                for row in decisions
                if row["policy_version_public_id"] == policy_version["public_id"]
            ),
            None,
        )
        if decision is None:
            decision = await api.request(
                "POST",
                f"/api/v2/release-candidates/{candidate['public_id']}/policy-evaluations",
                payload={
                    "namespace_id": namespace_id,
                    "policy_version_public_id": policy_version["public_id"],
                    "idempotency_key": f"e05-policy-{revision}",
                },
            )
        if decision["enforcement_outcome"] != "ALLOW":
            raise RuntimeError(
                f"real package PolicyDecision is {decision['enforcement_outcome']}: "
                f"{decision['reason_codes']}"
            )

        promotions = await api.request(
            "GET",
            "/api/v2/release-promotions",
            params={"namespace_id": namespace_id},
        )
        promotion = next(
            (row for row in promotions if row["candidate_public_id"] == candidate["public_id"]),
            None,
        )
        if promotion is None:
            promotion = await api.request(
                "POST",
                "/api/v2/release-promotions",
                payload={
                    "namespace_id": namespace_id,
                    "candidate_public_id": candidate["public_id"],
                    "policy_decision_public_id": decision["public_id"],
                    "strategy": "ALL_AT_ONCE",
                    "acknowledge_warnings": False,
                    "idempotency_key": f"e05-promotion-{revision}",
                },
            )

        receipt: dict[str, Any] | None = None
        if promotion["status"] == "DISPATCHED":
            issued = await api.request(
                "POST",
                "/api/v2/release-receipt-credentials",
                payload={
                    "namespace_id": namespace_id,
                    "runtime_id": runtime_id,
                    "device_id": f"hermes-e05-{runtime_id}",
                    "name": "Hermes E05 one-shot receipt verifier",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=15)
                    ).isoformat(),
                },
            )
            receipt = await api.request(
                "POST",
                "/api/v2/reporter/promotion-receipts",
                payload={
                    "dispatch_public_id": promotion["dispatch_public_id"],
                    "kind": "PROMOTION",
                    "external_receipt_id": f"hermes-e05-{revision}",
                    "status": "APPLIED",
                    "observed_package_version_public_id": args.package_version_public_id,
                    "observed_deployment_revision": deployment["revision"],
                    "observed_configuration_digest": configuration_digest,
                    "runtime_release_ref": f"hermes-models-{configuration_digest[:16]}",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "idempotency_key": f"hermes-e05-receipt-{revision}",
                },
                headers={"Authorization": f"Bearer {issued['token']}"},
            )
            if receipt["status"] != "APPLIED":
                raise RuntimeError(f"Runtime receipt was persisted as {receipt['status']}")
            await api.request(
                "POST",
                f"/api/v2/release-receipt-credentials/{issued['id']}/revoke",
                payload={"reason": "one_shot_e05_validation_complete"},
            )
            promotion = await api.request(
                "GET",
                f"/api/v2/release-promotions/{promotion['public_id']}",
            )

        if promotion["status"] not in {"SUCCEEDED", "ROLLED_BACK"}:
            raise RuntimeError(f"promotion ended in unexpected state {promotion['status']}")

        receipts = await api.request(
            "GET",
            "/api/v2/release-deployment-receipts",
            params={"namespace_id": namespace_id},
        )
        receipt = next(
            (row for row in receipts if row["dispatch_public_id"] == promotion["dispatch_public_id"]),
            None,
        )
        releases = await api.request(
            "GET",
            "/api/v2/release-environment-releases",
            params={
                "namespace_id": namespace_id,
                "environment_public_id": environment["public_id"],
            },
        )
        active_release = next(
            (
                row
                for row in releases
                if row["candidate_public_id"] == candidate["public_id"]
                and row["status"] == "ACTIVE"
            ),
            None,
        )
        if receipt is None or receipt["status"] != "APPLIED" or active_release is None:
            raise RuntimeError("promotion lacks an APPLIED receipt or ACTIVE environment release")

        # Exercise the real canary and rollback protocol against the same live
        # Hermes Runtime. The verifier records one metadata-only failed Run
        # through the normal execution.write API so the canary decision is
        # based on the production ingestion path rather than a database fixture.
        canary_configuration_digest = _canonical_digest(
            {
                "hermes_configuration_digest": configuration_digest,
                "validation_lane": "canary-failure-rollback",
            }
        )
        canary_scope = _canonical_digest(
            {
                "external_deployment_id": args.canary_external_deployment_id,
                "environment": args.environment_name,
            }
        )[:8]
        canary_revision = (
            f"hermes-e05-canary-runtime-{runtime_id}-{canary_scope}-"
            f"{canary_configuration_digest[:12]}"
        )
        deployments = await api.request(
            "GET",
            "/api/v1/deployments",
            params={"namespace_id": namespace_id},
        )
        canary_deployment = next(
            (
                row
                for row in deployments
                if row["external_deployment_id"] == args.canary_external_deployment_id
                and row["revision"] == canary_revision
                and row["runtime_id"] == runtime_id
            ),
            None,
        )
        if canary_deployment is None:
            canary_deployment = await api.request(
                "POST",
                "/api/v1/deployments",
                payload={
                    "namespace_id": namespace_id,
                    "runtime_id": runtime_id,
                    "agent_asset_id": agent_asset_id,
                    "package_version_public_id": args.package_version_public_id,
                    "external_deployment_id": args.canary_external_deployment_id,
                    "environment": args.environment_name,
                    "revision": canary_revision,
                    "configuration_digest": canary_configuration_digest,
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

        candidates = await api.request(
            "GET",
            "/api/v2/release-candidates",
            params={"namespace_id": namespace_id},
        )
        canary_candidate = next(
            (
                row
                for row in candidates
                if row["deployment_public_id"] == canary_deployment["public_id"]
            ),
            None,
        )
        if canary_candidate is None:
            if canary_deployment["status"] != "REGISTERED":
                raise RuntimeError(
                    "canary Deployment is not REGISTERED and has no ReleaseCandidate"
                )
            canary_candidate = await api.request(
                "POST",
                "/api/v2/release-candidates",
                payload={
                    "namespace_id": namespace_id,
                    "package_version_public_id": args.package_version_public_id,
                    "deployment_public_id": canary_deployment["public_id"],
                    "target_environment_public_id": environment["public_id"],
                    "baseline_candidate_public_id": None,
                    "idempotency_key": f"e05-canary-candidate-{canary_revision}",
                },
            )

        decisions = await api.request(
            "GET",
            "/api/v2/release-policy-decisions",
            params={
                "namespace_id": namespace_id,
                "candidate_public_id": canary_candidate["public_id"],
            },
        )
        canary_decision = next(
            (
                row
                for row in decisions
                if row["policy_version_public_id"] == policy_version["public_id"]
            ),
            None,
        )
        if canary_decision is None:
            canary_decision = await api.request(
                "POST",
                f"/api/v2/release-candidates/{canary_candidate['public_id']}/policy-evaluations",
                payload={
                    "namespace_id": namespace_id,
                    "policy_version_public_id": policy_version["public_id"],
                    "idempotency_key": f"e05-canary-policy-{canary_revision}",
                },
            )
        if canary_decision["enforcement_outcome"] != "ALLOW":
            raise RuntimeError(
                "canary candidate was blocked before dispatch: "
                f"{canary_decision['reason_codes']}"
            )

        promotions = await api.request(
            "GET",
            "/api/v2/release-promotions",
            params={"namespace_id": namespace_id},
        )
        canary_promotion = next(
            (
                row
                for row in promotions
                if row["candidate_public_id"] == canary_candidate["public_id"]
            ),
            None,
        )
        if canary_promotion is None:
            canary_promotion = await api.request(
                "POST",
                "/api/v2/release-promotions",
                payload={
                    "namespace_id": namespace_id,
                    "candidate_public_id": canary_candidate["public_id"],
                    "policy_decision_public_id": canary_decision["public_id"],
                    "strategy": "CANARY",
                    "acknowledge_warnings": False,
                    "canary": {
                        "minimum_completed_runs": 1,
                        "maximum_failure_rate": 0,
                        "maximum_untrusted_rate": 1,
                        "observation_window_seconds": 30,
                    },
                    "idempotency_key": f"e05-canary-promotion-{canary_revision}",
                },
            )

        if canary_promotion["status"] == "DISPATCHED":
            issued = await api.request(
                "POST",
                "/api/v2/release-receipt-credentials",
                payload={
                    "namespace_id": namespace_id,
                    "runtime_id": runtime_id,
                    "device_id": f"hermes-e05-canary-{runtime_id}",
                    "name": "Hermes E05 canary receipt verifier",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=15)
                    ).isoformat(),
                },
            )
            try:
                canary_receipt = await api.request(
                    "POST",
                    "/api/v2/reporter/promotion-receipts",
                    payload={
                        "dispatch_public_id": canary_promotion["dispatch_public_id"],
                        "kind": "PROMOTION",
                        "external_receipt_id": f"hermes-e05-canary-{canary_revision}",
                        "status": "APPLIED",
                        "observed_package_version_public_id": args.package_version_public_id,
                        "observed_deployment_revision": canary_revision,
                        "observed_configuration_digest": canary_configuration_digest,
                        "runtime_release_ref": (
                            f"hermes-canary-{canary_configuration_digest[:16]}"
                        ),
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                        "idempotency_key": f"hermes-e05-canary-receipt-{canary_revision}",
                    },
                    headers={"Authorization": f"Bearer {issued['token']}"},
                )
                if canary_receipt["status"] != "APPLIED":
                    raise RuntimeError(
                        f"canary Runtime receipt was persisted as {canary_receipt['status']}"
                    )
            finally:
                await api.request(
                    "POST",
                    f"/api/v2/release-receipt-credentials/{issued['id']}/revoke",
                    payload={"reason": "one_shot_e05_canary_receipt_complete"},
                )
            canary_promotion = await api.request(
                "GET",
                f"/api/v2/release-promotions/{canary_promotion['public_id']}",
            )

        canary_evaluation: dict[str, Any] | None = None
        if canary_promotion["status"] == "OBSERVING":
            now = datetime.now(timezone.utc)
            run_page = await api.request(
                "GET",
                "/api/v2/agent-runs",
                params={
                    "namespace_id": namespace_id,
                    "deployment_public_id": canary_deployment["public_id"],
                    "started_after": (now - timedelta(hours=1)).isoformat(),
                    "started_before": (now + timedelta(hours=1)).isoformat(),
                    "limit": 50,
                },
            )
            external_run_id = f"e05-canary-failure-{canary_revision}"
            run = next(
                (
                    row
                    for row in run_page["items"]
                    if row["external_run_id"] == external_run_id
                ),
                None,
            )
            if run is None or run["status"] not in {"FAILED", "SUCCEEDED", "CANCELLED"}:
                execution_credential_id, execution_token = (
                    await _issue_execution_credential(
                        runtime_id=runtime_id,
                        username=args.username,
                    )
                )
                try:
                    if run is None:
                        run = await api.request(
                            "POST",
                            "/api/v2/reporter/runs",
                            payload={
                                "external_run_id": external_run_id,
                                "deployment_public_id": canary_deployment["public_id"],
                                "attempt": 1,
                                "source_schema": "duckdock-run-envelope",
                                "source_schema_version": "1.0",
                                "started_at": datetime.now(timezone.utc).isoformat(),
                                "content_capture_mode": "metadata_only",
                                "metadata": {
                                    "operation.name": "e05.canary.validation",
                                },
                            },
                            headers={
                                "Authorization": f"Bearer {execution_token}",
                                "Idempotency-Key": f"e05-canary-run-start-{canary_revision}",
                            },
                        )
                    run = await api.request(
                        "POST",
                        f"/api/v2/reporter/runs/{run['run_public_id']}/complete",
                        payload={
                            # MySQL DATETIME without fractional precision may round a
                            # just-created started_at into the next second. Derive a
                            # terminal timestamp from the persisted API value so a
                            # fast local validation cannot end before that rounded
                            # timestamp.
                            "ended_at": (
                                datetime.fromisoformat(
                                    run["started_at"].replace("Z", "+00:00")
                                )
                                + timedelta(seconds=1)
                            ).isoformat(),
                            "status": "FAILED",
                            "error_type": "e05_canary_validation_failure",
                        },
                        headers={
                            "Authorization": f"Bearer {execution_token}",
                            "Idempotency-Key": f"e05-canary-run-complete-{canary_revision}",
                        },
                    )
                finally:
                    await _revoke_execution_credential(
                        credential_id=execution_credential_id,
                        username=args.username,
                    )
            if run is None or run["status"] != "FAILED":
                raise RuntimeError("canary validation Run did not reach FAILED")
            # Canary windows are persisted with database-second precision.
            # Wait through the first durable second before evaluating it.
            await asyncio.sleep(1.05)
            canary_evaluation = await api.request(
                "POST",
                f"/api/v2/release-promotions/{canary_promotion['public_id']}/canary-evaluations",
                payload={
                    "namespace_id": namespace_id,
                    "idempotency_key": f"e05-canary-evaluation-{canary_revision}",
                },
            )
            if canary_evaluation["outcome"] != "FAIL":
                raise RuntimeError(
                    f"expected FAIL canary outcome, got {canary_evaluation['outcome']}"
                )
            canary_promotion = await api.request(
                "GET",
                f"/api/v2/release-promotions/{canary_promotion['public_id']}",
            )

        rollbacks = await api.request(
            "GET",
            "/api/v2/release-rollbacks",
            params={"namespace_id": namespace_id},
        )
        rollback = next(
            (
                row
                for row in rollbacks
                if row["promotion_public_id"] == canary_promotion["public_id"]
            ),
            None,
        )
        if canary_promotion["status"] == "ROLLBACK_REQUESTED":
            if rollback is None or rollback["status"] != "DISPATCHED":
                raise RuntimeError("failed canary did not create a rollback dispatch")
            issued = await api.request(
                "POST",
                "/api/v2/release-receipt-credentials",
                payload={
                    "namespace_id": namespace_id,
                    "runtime_id": runtime_id,
                    "device_id": f"hermes-e05-rollback-{runtime_id}",
                    "name": "Hermes E05 rollback receipt verifier",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=15)
                    ).isoformat(),
                },
            )
            try:
                rollback_receipt = await api.request(
                    "POST",
                    "/api/v2/reporter/promotion-receipts",
                    payload={
                        "dispatch_public_id": rollback["dispatch_public_id"],
                        "kind": "ROLLBACK",
                        "external_receipt_id": f"hermes-e05-rollback-{canary_revision}",
                        "status": "APPLIED",
                        "observed_package_version_public_id": args.package_version_public_id,
                        "observed_deployment_revision": deployment["revision"],
                        "observed_configuration_digest": configuration_digest,
                        "runtime_release_ref": (
                            f"hermes-restored-{configuration_digest[:16]}"
                        ),
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                        "idempotency_key": f"hermes-e05-rollback-receipt-{canary_revision}",
                    },
                    headers={"Authorization": f"Bearer {issued['token']}"},
                )
                if rollback_receipt["status"] != "APPLIED":
                    raise RuntimeError(
                        f"rollback Runtime receipt was persisted as {rollback_receipt['status']}"
                    )
            finally:
                await api.request(
                    "POST",
                    f"/api/v2/release-receipt-credentials/{issued['id']}/revoke",
                    payload={"reason": "one_shot_e05_rollback_receipt_complete"},
                )
            rollback = await api.request(
                "GET",
                f"/api/v2/release-rollbacks/{rollback['public_id']}",
            )
            canary_promotion = await api.request(
                "GET",
                f"/api/v2/release-promotions/{canary_promotion['public_id']}",
            )

        if (
            canary_promotion["status"] != "ROLLED_BACK"
            or rollback is None
            or rollback["status"] != "SUCCEEDED"
        ):
            raise RuntimeError(
                "real canary/rollback drill did not reach ROLLED_BACK/SUCCEEDED"
            )

        receipts = await api.request(
            "GET",
            "/api/v2/release-deployment-receipts",
            params={"namespace_id": namespace_id},
        )
        canary_receipt = next(
            (
                row
                for row in receipts
                if row["dispatch_public_id"] == canary_promotion["dispatch_public_id"]
            ),
            None,
        )
        rollback_receipt = next(
            (
                row
                for row in receipts
                if row["dispatch_public_id"] == rollback["dispatch_public_id"]
            ),
            None,
        )
        if canary_receipt is None or rollback_receipt is None:
            raise RuntimeError("canary or rollback APPLIED receipt is missing")
        canary_evaluations = await api.request(
            "GET",
            "/api/v2/release-canary-evaluations",
            params={"namespace_id": namespace_id},
        )
        canary_evaluation = next(
            (
                row
                for row in canary_evaluations
                if row["promotion_public_id"] == canary_promotion["public_id"]
            ),
            None,
        )
        if canary_evaluation is None or canary_evaluation["outcome"] != "FAIL":
            raise RuntimeError("persisted FAIL canary evaluation is missing")

        return {
            "ok": True,
            "namespace_id": namespace_id,
            "runtime_id": runtime_id,
            "hermes_model_count": len(model_ids),
            "hermes_model_ids": model_ids,
            "package_version_public_id": args.package_version_public_id,
            "deployment_public_id": deployment["public_id"],
            "deployment_revision": deployment["revision"],
            "configuration_digest": configuration_digest,
            "environment_public_id": environment["public_id"],
            "candidate_public_id": candidate["public_id"],
            "policy_decision_public_id": decision["public_id"],
            "policy_outcome": decision["enforcement_outcome"],
            "promotion_public_id": promotion["public_id"],
            "promotion_status": promotion["status"],
            "receipt_public_id": receipt["public_id"],
            "receipt_status": receipt["status"],
            "environment_release_public_id": active_release["public_id"],
            "canary_deployment_public_id": canary_deployment["public_id"],
            "canary_candidate_public_id": canary_candidate["public_id"],
            "canary_promotion_public_id": canary_promotion["public_id"],
            "canary_promotion_status": canary_promotion["status"],
            "canary_evaluation_public_id": canary_evaluation["public_id"],
            "canary_receipt_public_id": canary_receipt["public_id"],
            "rollback_public_id": rollback["public_id"],
            "rollback_status": rollback["status"],
            "rollback_receipt_public_id": rollback_receipt["public_id"],
        }
    finally:
        await api.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdock-url", default="http://127.0.0.1:8801")
    parser.add_argument(
        "--hermes-models-url",
        default="http://host.docker.internal:50070/v1/models",
    )
    parser.add_argument("--package-version-public-id", default=DEFAULT_PACKAGE_VERSION)
    parser.add_argument("--runtime-id", type=int, default=9)
    parser.add_argument("--environment-name", default="hermes-e05-dev")
    parser.add_argument("--external-deployment-id", default="duckdock-e05-hermes-live")
    parser.add_argument(
        "--canary-external-deployment-id",
        default="duckdock-e05-hermes-canary-live",
    )
    parser.add_argument("--policy-name", default="hermes-e05-release")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument(
        "--password",
        default=os.getenv("E05_VALIDATOR_PASSWORD", DEFAULT_PASSWORD),
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
