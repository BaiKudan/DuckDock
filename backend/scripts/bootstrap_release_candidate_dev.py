#!/usr/bin/env python3
"""Bootstrap a repeatable DuckDock RC validation subject through live APIs.

The script is intended for a fresh, isolated development stack. It creates the
minimum Namespace/Runtime/Agent/Package graph needed by the E05-E09 live gates,
signs the PackageVersion locally with a deterministic validation-only Ed25519
key, and verifies the stored SBOM/signature through the product API. No private
key is persisted or sent to DuckDock.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.schemas.package_registry import AgentPackageManifestV2
from app.services.package_registry_service import (
    canonical_json_bytes,
    canonical_manifest_bytes,
)


DEFAULT_USERNAME = "rc1-bootstrap-admin"
DEFAULT_PASSWORD = "DuckDock@RC1Bootstrap2026!"
DEFAULT_NAMESPACE = "duckdock-rc1-validation"
DEFAULT_RUNTIME = "Hermes RC1 validation runtime"
DEFAULT_ASSET_EXTERNAL_ID = "hermes-rc1-validation-agent"
DEFAULT_PACKAGE = "hermes-rc1-validation-agent"


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
        expected: set[int] | None = None,
        authenticated: bool = True,
    ) -> Any:
        response = await self.client.request(
            method,
            path,
            json=payload,
            params=params,
            headers=self.headers if authenticated else {},
        )
        if response.status_code not in (expected or {200, 201, 204}):
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text[:500]
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {detail}")
        return None if response.status_code == 204 else response.json()

    async def login(self, username: str, password: str) -> None:
        token = await self.request(
            "POST",
            "/api/v1/auth/login",
            payload={"username": username, "password": password},
            authenticated=False,
        )
        self.headers = {"Authorization": f"Bearer {token['access_token']}"}


def _private_key(namespace_name: str) -> Ed25519PrivateKey:
    seed = hashlib.sha256(
        f"duckdock:rc1:validation-only:{namespace_name}".encode("utf-8")
    ).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def _sbom(*digests: str) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "components": [
            {
                "type": "application",
                "name": f"rc1-component-{index}",
                "version": "2.0.0-rc.1",
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
            for index, digest in enumerate(digests)
        ],
    }


def _manifest(
    *,
    package: dict[str, Any],
    namespace: dict[str, Any],
    asset: dict[str, Any],
    sbom: dict[str, Any],
) -> AgentPackageManifestV2:
    skill_digest = "a" * 64
    prompt_digest = "b" * 64
    sbom_digest = hashlib.sha256(canonical_json_bytes(sbom)).hexdigest()
    return AgentPackageManifestV2.model_validate(
        {
            "schema_version": "2.0",
            "package_id": package["public_id"],
            "package_version": "2.0.0-rc.1",
            "namespace_id": namespace["name"],
            "agent": {
                "asset_id": asset["external_id"],
                "name": asset["name"],
                "description": "Real local Hermes RC validation subject",
            },
            "runtime": {
                "harness": "hermes",
                "entrypoint": "agents/duckdock-rc1.yaml",
                "minimum_harness_version": "1.0.0",
                "capabilities": ["model_call", "tool_call"],
            },
            "artifacts": [
                {
                    "type": "skill",
                    "asset_id": "duckdock-rc1-skill",
                    "version_id": "duckdock-rc1-skill-v1",
                    "uri": "s3://duckdock-rc1/packages/skill.zip",
                    "sha256": skill_digest,
                    "media_type": "application/zip",
                },
                {
                    "type": "prompt",
                    "asset_id": "duckdock-rc1-prompt",
                    "version_id": "duckdock-rc1-prompt-v1",
                    "uri": "s3://duckdock-rc1/packages/prompt.json",
                    "sha256": prompt_digest,
                    "media_type": "application/json",
                },
            ],
            "provenance": {
                "source_repository": "https://github.com/BaiKudan/DuckDock",
                "source_revision": "674bc92",
                "built_at": datetime.now(timezone.utc).isoformat(),
                "builder_id": "duckdock-rc1-live-validator",
                "build_id": "duckdock-2.0.0-rc.1-local",
            },
            "telemetry": {
                "schema_version": "1.0",
                "content_policy": "metadata_only",
                "required_attributes": [
                    "duckdock.namespace_id",
                    "duckdock.deployment_revision",
                    "duckdock.component_digests",
                ],
            },
            "evaluation_policy_id": "duckdock-rc1-quality-policy",
            "component_graph": {
                "edges": [
                    {
                        "from_component": "skill:duckdock-rc1-skill:duckdock-rc1-skill-v1",
                        "to_component": "prompt:duckdock-rc1-prompt:duckdock-rc1-prompt-v1",
                        "relationship": "uses",
                    }
                ]
            },
            "sbom": {
                "format": "cyclonedx-json",
                "spec_version": "1.6",
                "document_sha256": sbom_digest,
                "media_type": "application/vnd.cyclonedx+json",
            },
            "annotations": {
                "owner": "duckdock-release-engineering",
                "risk_tier": "validation-only",
            },
        }
    )


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        hermes_response = await client.get(args.hermes_models_url)
        hermes_response.raise_for_status()
        hermes_models = sorted(
            item["id"]
            for item in hermes_response.json().get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
        if not hermes_models:
            raise RuntimeError("local Hermes returned no model identities")
        hermes_inference: dict[str, Any] | None = None
        if args.verify_hermes_embedding:
            inference_response = await client.post(
                args.hermes_models_url.removesuffix("/v1/models")
                + "/v1/embeddings",
                json={
                    "model": hermes_models[0],
                    "input": "DuckDock 2.0.0-rc.1 real Hermes validation",
                },
            )
            inference_response.raise_for_status()
            inference_data = inference_response.json().get("data", [])
            embedding = (
                inference_data[0].get("embedding")
                if inference_data and isinstance(inference_data[0], dict)
                else None
            )
            if not isinstance(embedding, list) or not embedding or not all(
                isinstance(value, (int, float)) for value in embedding
            ):
                raise RuntimeError("Hermes embedding response was not a numeric vector")
            hermes_inference = {
                "endpoint": "/v1/embeddings",
                "status_code": inference_response.status_code,
                "vector_dimensions": len(embedding),
                "content_capture": "shape_only",
            }
    api = Api(args.duckdock_url)
    try:
        registration = await api.client.post(
            "/api/v1/auth/register",
            json={
                "username": args.username,
                "email": f"{args.username}@duckdock.dev",
                "password": args.password,
                "full_name": "DuckDock RC Validator",
            },
        )
        if registration.status_code not in {201, 409}:
            raise RuntimeError(
                f"POST /api/v1/auth/register -> {registration.status_code}: "
                f"{registration.text[:500]}"
            )
        await api.login(args.username, args.password)

        namespaces = await api.request("GET", "/api/v1/namespaces")
        namespace = next(
            (row for row in namespaces if row["name"] == args.namespace_name), None
        )
        if namespace is None:
            namespace = await api.request(
                "POST",
                "/api/v1/namespaces",
                payload={
                    "name": args.namespace_name,
                    "description": "Isolated DuckDock 2.0.0-rc.1 live validation",
                },
            )

        runtimes = await api.request("GET", "/api/v1/runtimes")
        runtime = next(
            (
                row
                for row in runtimes
                if row["namespace_id"] == namespace["id"]
                and row["name"] == args.runtime_name
            ),
            None,
        )
        if runtime is None:
            runtime = await api.request(
                "POST",
                "/api/v1/runtimes",
                payload={
                    "namespace_id": namespace["id"],
                    "provider": "custom",
                    "name": args.runtime_name,
                    "base_url": args.hermes_models_url.removesuffix("/v1/models"),
                    "deploy_type": "on_prem",
                    "metadata_json": {
                        "adapter_profile": "hermes-reporter",
                        "validation_lane": "2.0.0-rc.1",
                    },
                },
            )

        assets = await api.request("GET", "/api/v1/assets")
        asset = next(
            (
                row
                for row in assets
                if row["namespace_id"] == namespace["id"]
                and row["external_id"] == args.asset_external_id
            ),
            None,
        )
        if asset is None:
            asset = await api.request(
                "POST",
                "/api/v1/assets",
                payload={
                    "namespace_id": namespace["id"],
                    "asset_type": "agent",
                    "name": "Hermes RC1 validation agent",
                    "description": "Metadata-only real Hermes validation asset",
                    "source_provider": "custom",
                    "source_runtime_id": runtime["id"],
                    "external_id": args.asset_external_id,
                    "criticality": "high",
                    "metadata_json": {
                        "harness": "hermes",
                        "content_policy": "metadata_only",
                    },
                },
            )

        private_key = _private_key(namespace["name"])
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        keys = await api.request(
            "GET",
            "/api/v2/package-signing-keys",
            params={"namespace_id": namespace["id"]},
        )
        signing_key = next(
            (
                row
                for row in keys
                if row["key_id"] == "duckdock-rc1-validation"
                and row["status"] == "ACTIVE"
            ),
            None,
        )
        if signing_key is None:
            signing_key = await api.request(
                "POST",
                "/api/v2/package-signing-keys",
                payload={
                    "namespace_id": namespace["id"],
                    "key_id": "duckdock-rc1-validation",
                    "algorithm": "ED25519",
                    "public_key_pem": public_pem,
                },
            )

        packages = await api.request(
            "GET",
            "/api/v2/agent-packages",
            params={"namespace_id": namespace["id"]},
        )
        package = next(
            (row for row in packages if row["name"] == args.package_name), None
        )
        if package is None:
            package = await api.request(
                "POST",
                "/api/v2/agent-packages",
                payload={
                    "namespace_id": namespace["id"],
                    "name": args.package_name,
                    "description": "Signed real Hermes RC validation package",
                    "agent_asset_id": asset["id"],
                },
            )

        versions = await api.request(
            "GET", f"/api/v2/agent-packages/{package['public_id']}/versions"
        )
        version = next(
            (
                row
                for row in versions
                if row["manifest"]["package_version"] == "2.0.0-rc.1"
            ),
            None,
        )
        if version is None:
            skill_digest = "a" * 64
            prompt_digest = "b" * 64
            sbom = _sbom(skill_digest, prompt_digest)
            manifest = _manifest(
                package=package, namespace=namespace, asset=asset, sbom=sbom
            )
            signature = base64.b64encode(
                private_key.sign(canonical_manifest_bytes(manifest))
            ).decode("ascii")
            version = await api.request(
                "POST",
                "/api/v2/agent-package-versions",
                payload={
                    "namespace_id": namespace["id"],
                    "package_public_id": package["public_id"],
                    "signing_key_public_id": signing_key["public_id"],
                    "signature": signature,
                    "idempotency_key": "duckdock-rc1-validation-package-v1",
                    "manifest": manifest.model_dump(mode="json"),
                    "sbom_document": sbom,
                },
            )

        verification = await api.request(
            "POST", f"/api/v2/agent-package-versions/{version['public_id']}/verify"
        )
        if not all(
            verification[key]
            for key in (
                "verified",
                "signature_valid",
                "sbom_digest_valid",
                "sbom_component_coverage_valid",
            )
        ):
            raise RuntimeError(f"PackageVersion verification failed: {verification}")

        deployments = await api.request(
            "GET",
            "/api/v1/deployments",
            params={"namespace_id": namespace["id"]},
        )
        inventory_deployment = next(
            (
                row
                for row in deployments
                if row["runtime_id"] == runtime["id"]
                and row["external_deployment_id"]
                == "duckdock-rc1-bootstrap-inventory"
            ),
            None,
        )
        if inventory_deployment is None:
            configuration_digest = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "hermes_models": hermes_models,
                        "content_policy": "metadata_only",
                        "lane": "2.0.0-rc.1-bootstrap",
                    }
                )
            ).hexdigest()
            inventory_deployment = await api.request(
                "POST",
                "/api/v1/deployments",
                payload={
                    "namespace_id": namespace["id"],
                    "runtime_id": runtime["id"],
                    "agent_asset_id": asset["id"],
                    "package_version_public_id": version["public_id"],
                    "external_deployment_id": "duckdock-rc1-bootstrap-inventory",
                    "environment": "inventory",
                    "revision": "duckdock-2.0.0-rc.1-bootstrap",
                    "configuration_digest": configuration_digest,
                    "components": [
                        {
                            "component_key": "agent",
                            "component_role": "agent",
                            "ai_asset_id": asset["id"],
                            "content_digest": version["manifest_digest"],
                            "configuration": {
                                "provider": "custom",
                                "endpoint_kind": "openai-compatible",
                            },
                        }
                    ],
                },
            )

        return {
            "ok": True,
            "hermes_models": hermes_models,
            "hermes_inference": hermes_inference,
            "namespace_id": namespace["id"],
            "runtime_id": runtime["id"],
            "asset_id": asset["id"],
            "package_public_id": package["public_id"],
            "package_version_public_id": version["public_id"],
            "package_status": version["status"],
            "inventory_deployment_public_id": inventory_deployment["public_id"],
            "manifest_digest": version["manifest_digest"],
            "signing_key_fingerprint": signing_key["public_key_fingerprint"],
            "verification": verification,
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
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--namespace-name", default=DEFAULT_NAMESPACE)
    parser.add_argument("--runtime-name", default=DEFAULT_RUNTIME)
    parser.add_argument("--asset-external-id", default=DEFAULT_ASSET_EXTERNAL_ID)
    parser.add_argument("--package-name", default=DEFAULT_PACKAGE)
    parser.add_argument(
        "--verify-hermes-embedding",
        action="store_true",
        help="Call the local OpenAI-compatible /v1/embeddings endpoint and retain shape only.",
    )
    return parser.parse_args()


def main() -> int:
    result = asyncio.run(verify(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
