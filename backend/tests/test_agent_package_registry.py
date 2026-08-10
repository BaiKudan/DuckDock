from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.control_plane import (
    AIAsset,
    AssetType,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.deployment import DeploymentComponentRole
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.package_registry import PackageSigningKeyStatus
from app.models.user import SystemRole, User
from app.schemas.deployment import AgentDeploymentCreate, DeploymentComponentCreate
from app.schemas.package_registry import (
    AgentPackageCreate,
    AgentPackageManifestV2,
    AgentPackageVersionCreate,
    PackageSigningKeyCreate,
)
from app.services.deployment_service import DeploymentTenantMismatchError, register_deployment
from app.services.package_registry_service import (
    PackageGraphError,
    PackageRegistryReferenceError,
    PackageSbomError,
    PackageSignatureError,
    canonical_json_bytes,
    canonical_manifest_bytes,
    create_package,
    create_signing_key,
    package_sbom_download,
    register_package_version,
    revoke_signing_key,
    to_package_version_out,
    verify_package_version,
)


class FakePackageStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def package_sbom_object_key(
        self,
        *,
        namespace_id: int,
        package_public_id: str,
        package_version: str,
        sha256: str,
    ) -> str:
        return f"package-registry/sboms/{namespace_id}/{package_public_id}/{package_version}/{sha256}.json"

    async def put_object_bytes_async(
        self, *, object_key: str, payload: bytes, content_type: str, metadata: dict[str, str]
    ) -> dict:
        assert content_type == "application/vnd.cyclonedx+json"
        assert metadata["sha256"]
        self.objects[object_key] = payload
        return {"object_key": object_key, "size_bytes": len(payload)}

    async def delete_object_async(self, object_key: str) -> None:
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)

    async def read_object_bytes_async(self, object_key: str, *, max_bytes: int | None = None) -> bytes:
        payload = self.objects[object_key]
        assert max_bytes is None or len(payload) <= max_bytes
        return payload

    def generate_presigned_get_url(self, *, object_key: str) -> dict:
        assert object_key in self.objects
        return {
            "download_url": f"https://objects.example.test/{object_key}",
            "expires_in": 900,
            "expires_at": datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc),
        }


async def _seed(db, suffix: str = "main"):
    actor = User(
        username=f"package-{suffix}",
        email=f"package-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add(actor)
    await db.flush()
    namespace = Namespace(name=f"package-{suffix}", owner_id=actor.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=actor.id,
            role=NamespaceRole.ADMIN,
        )
    )
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.AGENT,
        name=f"package-agent-{suffix}",
        source_provider=RuntimeProvider.CUSTOM,
        external_id=f"agent-package-{suffix}",
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"package-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add_all([asset, runtime])
    await db.flush()
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    key = await create_signing_key(
        db,
        request=PackageSigningKeyCreate(
            namespace_id=namespace.id,
            key_id=f"ci-{suffix}",
            public_key_pem=public_pem,
        ),
        actor=actor,
    )
    package = await create_package(
        db,
        request=AgentPackageCreate(
            namespace_id=namespace.id,
            name=f"research-agent-{suffix}",
            description="signed package test",
            agent_asset_id=asset.id,
        ),
        actor=actor,
    )
    return actor, namespace, asset, runtime, private_key, key, package


def _sbom(*digests: str) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "components": [
            {
                "type": "application",
                "name": f"component-{index}",
                "version": "1.0.0",
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
            for index, digest in enumerate(digests)
        ],
    }


def _manifest(package, namespace, asset, sbom_document: dict, *, version: str = "1.0.0"):
    skill_digest = "a" * 64
    prompt_digest = "b" * 64
    sbom_digest = __import__("hashlib").sha256(canonical_json_bytes(sbom_document)).hexdigest()
    return AgentPackageManifestV2.model_validate(
        {
            "schema_version": "2.0",
            "package_id": package.public_id,
            "package_version": version,
            "namespace_id": namespace.name,
            "agent": {"asset_id": asset.external_id, "name": asset.name},
            "runtime": {
                "harness": "hermes",
                "entrypoint": "agents/research.yaml",
                "minimum_harness_version": "1.0.0",
                "capabilities": ["model_call", "tool_call"],
            },
            "artifacts": [
                {
                    "type": "skill",
                    "asset_id": "skill-research",
                    "version_id": "skill-research-v1",
                    "uri": "s3://packages/research/skill.zip",
                    "sha256": skill_digest,
                    "media_type": "application/zip",
                },
                {
                    "type": "prompt",
                    "asset_id": "prompt-system",
                    "version_id": "prompt-system-v1",
                    "uri": "s3://packages/research/prompt.json",
                    "sha256": prompt_digest,
                    "media_type": "application/json",
                },
            ],
            "provenance": {
                "source_repository": "https://github.com/example/research-agent",
                "source_revision": "1" * 40,
                "built_at": "2026-08-04T01:00:00Z",
                "builder_id": "ci-builder",
                "build_id": f"build-{version}",
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
            "evaluation_policy_id": "quality-policy-v1",
            "component_graph": {
                "edges": [
                    {
                        "from_component": "skill:skill-research:skill-research-v1",
                        "to_component": "prompt:prompt-system:prompt-system-v1",
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
            "annotations": {"owner": "agent-platform", "risk_tier": "medium"},
        }
    )


def _version_request(namespace, package, key, private_key, manifest, sbom_document, *, idem="pkg-v1"):
    signature = private_key.sign(canonical_manifest_bytes(manifest))
    return AgentPackageVersionCreate(
        namespace_id=namespace.id,
        package_public_id=package.public_id,
        signing_key_public_id=key.public_id,
        signature=base64.b64encode(signature).decode("ascii"),
        idempotency_key=idem,
        manifest=manifest,
        sbom_document=sbom_document,
    )


async def test_signed_package_version_is_immutable_idempotent_and_verifiable(async_session):
    actor, namespace, asset, runtime, private_key, key, package = await _seed(async_session)
    storage = FakePackageStorage()
    sbom = _sbom("a" * 64, "b" * 64, "c" * 64)
    manifest = _manifest(package, namespace, asset, sbom)
    request = _version_request(namespace, package, key, private_key, manifest, sbom)

    created = await register_package_version(
        async_session, request=request, actor=actor, storage=storage
    )
    # Exact replay is also the repair path when the content-addressed SBOM is
    # missing after a storage/database process interruption.
    storage.objects.pop(created.sbom.object_key)
    replay = await register_package_version(
        async_session, request=request, actor=actor, storage=storage
    )
    assert replay.public_id == created.public_id
    assert created.status.value == "VERIFIED"
    assert len(created.components) == 2
    assert len(created.dependencies) == 1
    assert created.sbom.component_count == 3
    assert len(storage.objects) == 1
    assert storage.objects[created.sbom.object_key] == canonical_json_bytes(sbom)

    output = to_package_version_out(created)
    assert output.package_public_id == package.public_id
    assert output.dependencies[0].relationship.value == "uses"
    assert output.signature == request.signature
    verified = await verify_package_version(
        async_session, public_id=created.public_id, storage=storage
    )
    assert verified.verified is True
    assert verified.signature_valid is True
    assert verified.sbom_digest_valid is True
    assert verified.sbom_component_coverage_valid is True
    download = await package_sbom_download(
        async_session, public_id=created.public_id, storage=storage
    )
    assert download.document_sha256 == manifest.sbom.document_sha256
    assert download.download_url.startswith("https://objects.example.test/")

    deployment = await register_deployment(
        async_session,
        request=AgentDeploymentCreate(
            namespace_id=namespace.id,
            runtime_id=runtime.id,
            agent_asset_id=asset.id,
            package_version_public_id=created.public_id,
            external_deployment_id="package-backed",
            environment="dev",
            revision="1",
            configuration_digest="d" * 64,
            components=[
                DeploymentComponentCreate(
                    component_key="agent",
                    component_role=DeploymentComponentRole.AGENT,
                    ai_asset_id=asset.id,
                )
            ],
        ),
        actor=actor,
    )
    assert deployment.package_version_public_id == created.public_id

    events = (
        await async_session.execute(
            select(OutboxEvent).order_by(OutboxEvent.id)
        )
    ).scalars().all()
    assert [event.event_type for event in events] == [
        "PackageSigningKeyRegistered",
        "AgentPackageCreated",
        "AgentPackageVersionRegistered",
    ]
    serialized_events = str([event.payload_json for event in events])
    assert request.signature not in serialized_events
    assert "BEGIN PUBLIC KEY" not in serialized_events
    audits = (
        await async_session.execute(select(AuditLog).order_by(AuditLog.id))
    ).scalars().all()
    serialized_audits = str([row.details for row in audits])
    assert request.signature not in serialized_audits
    assert "BEGIN PUBLIC KEY" not in serialized_audits
    assert str(sbom) not in serialized_audits


async def test_graph_sbom_signature_and_revoked_key_fail_closed(async_session):
    actor, namespace, asset, _, private_key, key, package = await _seed(async_session, "negative")
    storage = FakePackageStorage()
    sbom = _sbom("a" * 64, "b" * 64)
    manifest = _manifest(package, namespace, asset, sbom)

    cyclic_data = manifest.model_dump(mode="json")
    cyclic_data["component_graph"]["edges"].append(
        {
            "from_component": "prompt:prompt-system:prompt-system-v1",
            "to_component": "skill:skill-research:skill-research-v1",
            "relationship": "depends_on",
        }
    )
    cyclic = AgentPackageManifestV2.model_validate(cyclic_data)
    with pytest.raises(PackageGraphError, match="acyclic"):
        await register_package_version(
            async_session,
            request=_version_request(namespace, package, key, private_key, cyclic, sbom),
            actor=actor,
            storage=storage,
        )

    incomplete_sbom = _sbom("a" * 64)
    incomplete_manifest = _manifest(package, namespace, asset, incomplete_sbom)
    with pytest.raises(PackageSbomError, match="missing"):
        await register_package_version(
            async_session,
            request=_version_request(
                namespace, package, key, private_key, incomplete_manifest, incomplete_sbom
            ),
            actor=actor,
            storage=storage,
        )

    wrong_key = Ed25519PrivateKey.generate()
    with pytest.raises(PackageSignatureError, match="verification failed"):
        await register_package_version(
            async_session,
            request=_version_request(namespace, package, key, wrong_key, manifest, sbom),
            actor=actor,
            storage=storage,
        )

    revoked = await revoke_signing_key(async_session, public_id=key.public_id, actor=actor)
    assert revoked.status == PackageSigningKeyStatus.REVOKED
    with pytest.raises(PackageSignatureError, match="revoked"):
        await register_package_version(
            async_session,
            request=_version_request(namespace, package, key, private_key, manifest, sbom),
            actor=actor,
            storage=storage,
        )
    assert storage.objects == {}


async def test_package_and_deployment_pins_reject_cross_tenant_refs(async_session):
    actor, namespace, asset, _, private_key, key, package = await _seed(async_session, "tenant-a")
    other_actor, other_namespace, other_asset, other_runtime, *_ = await _seed(
        async_session, "tenant-b"
    )
    storage = FakePackageStorage()
    sbom = _sbom("a" * 64, "b" * 64)
    manifest = _manifest(package, namespace, asset, sbom)
    version = await register_package_version(
        async_session,
        request=_version_request(namespace, package, key, private_key, manifest, sbom),
        actor=actor,
        storage=storage,
    )

    with pytest.raises(DeploymentTenantMismatchError, match="PackageVersion"):
        await register_deployment(
            async_session,
            request=AgentDeploymentCreate(
                namespace_id=other_namespace.id,
                runtime_id=other_runtime.id,
                agent_asset_id=other_asset.id,
                package_version_public_id=version.public_id,
                external_deployment_id="cross-tenant",
                environment="dev",
                revision="1",
                configuration_digest="e" * 64,
                components=[
                    DeploymentComponentCreate(
                        component_key="agent",
                        component_role=DeploymentComponentRole.AGENT,
                        ai_asset_id=other_asset.id,
                    )
                ],
            ),
            actor=other_actor,
        )

    with pytest.raises(PackageRegistryReferenceError, match="Namespace"):
        await create_package(
            async_session,
            request=AgentPackageCreate(
                namespace_id=namespace.id,
                name="cross-tenant-agent",
                agent_asset_id=other_asset.id,
            ),
            actor=actor,
        )


async def test_package_verification_detects_tampered_sbom_object(async_session):
    actor, namespace, asset, _, private_key, key, package = await _seed(async_session, "tamper")
    storage = FakePackageStorage()
    sbom = _sbom("a" * 64, "b" * 64)
    manifest = _manifest(package, namespace, asset, sbom)
    created = await register_package_version(
        async_session,
        request=_version_request(namespace, package, key, private_key, manifest, sbom),
        actor=actor,
        storage=storage,
    )
    storage.objects[created.sbom.object_key] = b'{"tampered":true}'
    verification = await verify_package_version(
        async_session, public_id=created.public_id, storage=storage
    )
    assert verification.verified is False
    assert verification.signature_valid is True
    assert verification.sbom_digest_valid is False


def test_manifest_schema_rejects_content_bearing_extensions() -> None:
    assert "prompt" not in AgentPackageManifestV2.model_fields
