from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.control_plane import AIAsset, AssetType
from app.models.package_registry import (
    AgentPackage,
    AgentPackageStatus,
    AgentPackageVersion,
    AgentPackageVersionStatus,
    PackageComponent,
    PackageDependency,
    PackageDependencyRelationship,
    PackageSbom,
    PackageSbomFormat,
    PackageSignatureAlgorithm,
    PackageSigningKey,
    PackageSigningKeyStatus,
)
from app.models.user import User
from app.schemas.package_registry import (
    AgentPackageCreate,
    AgentPackageManifestV2,
    AgentPackageOut,
    AgentPackageVersionCreate,
    AgentPackageVersionOut,
    AgentPackageVersionVerificationOut,
    PackageComponentOut,
    PackageDependencyOut,
    PackageSbomDownloadOut,
    PackageSbomOut,
    PackageSigningKeyCreate,
    PackageSigningKeyRotate,
)
from app.services.artifact_service import ArtifactStorageService, artifact_service
from app.services.audit_service import audit
from app.services.outbox_event_service import (
    build_agent_package_created,
    build_agent_package_version_registered,
    build_package_signing_key_registered,
    build_package_signing_key_revoked,
    build_package_signing_key_rotated,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


MAX_SBOM_BYTES = 2 * 1024 * 1024
MAX_SBOM_COMPONENTS = 2048
_FORBIDDEN_SBOM_KEYS = {
    "prompt",
    "prompts",
    "message",
    "messages",
    "completion",
    "completions",
    "model_input",
    "model_output",
    "tool_arguments",
    "tool_result",
    "tool_results",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "private_key",
    "privatekey",
    "authorization",
    "token",
}


class PackageRegistryError(ValueError):
    pass


class PackageRegistryNotFoundError(PackageRegistryError):
    pass


class PackageRegistryConflictError(PackageRegistryError):
    pass


class PackageRegistryReferenceError(PackageRegistryError):
    pass


class PackageManifestError(PackageRegistryError):
    pass


class PackageGraphError(PackageManifestError):
    pass


class PackageSbomError(PackageManifestError):
    pass


class PackageSignatureError(PackageRegistryError):
    pass


class PackageStorageError(PackageRegistryError):
    pass


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_manifest_document(manifest: AgentPackageManifestV2) -> dict[str, Any]:
    return manifest.model_dump(mode="json", exclude_none=True)


def canonical_manifest_bytes(manifest: AgentPackageManifestV2) -> bytes:
    return canonical_json_bytes(canonical_manifest_document(manifest))


def manifest_digest(manifest: AgentPackageManifestV2) -> str:
    return _sha256(canonical_manifest_bytes(manifest))


def _canonical_public_key(public_key_pem: str) -> tuple[Ed25519PublicKey, str, str]:
    try:
        loaded = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise PackageSignatureError("public_key_pem is not a valid public key") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise PackageSignatureError("only Ed25519 public keys are accepted")
    canonical_pem = loaded.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    der = loaded.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return loaded, canonical_pem, _sha256(der)


def _decode_signature(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise PackageSignatureError("signature must be canonical base64") from exc
    if len(decoded) != 64:
        raise PackageSignatureError("Ed25519 signature must be exactly 64 bytes")
    if base64.b64encode(decoded).decode("ascii") != value:
        raise PackageSignatureError("signature must use canonical padded base64")
    return decoded


def _verify_signature(*, key: PackageSigningKey, signature_value: str, payload: bytes) -> bytes:
    if key.algorithm != PackageSignatureAlgorithm.ED25519:
        raise PackageSignatureError("unsupported signing algorithm")
    public_key, canonical_pem, fingerprint = _canonical_public_key(key.public_key_pem)
    if canonical_pem != key.public_key_pem or fingerprint != key.public_key_fingerprint:
        raise PackageSignatureError("stored signing key integrity check failed")
    signature = _decode_signature(signature_value)
    try:
        public_key.verify(signature, payload)
    except InvalidSignature as exc:
        raise PackageSignatureError("manifest signature verification failed") from exc
    return signature


def _component_refs(manifest: AgentPackageManifestV2) -> set[str]:
    return {artifact.component_ref for artifact in manifest.artifacts}


def _validate_graph(manifest: AgentPackageManifestV2) -> tuple[list[dict[str, str]], str]:
    node_refs = _component_refs(manifest)
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    adjacency: dict[str, set[str]] = {ref: set() for ref in node_refs}
    for edge in manifest.component_graph.edges:
        if edge.from_component not in node_refs or edge.to_component not in node_refs:
            raise PackageGraphError("component graph edge references an unknown component")
        if edge.from_component == edge.to_component:
            raise PackageGraphError("component graph self edges are forbidden")
        identity = (edge.from_component, edge.to_component, edge.relationship.value)
        if identity in seen:
            raise PackageGraphError("component graph contains a duplicate edge")
        seen.add(identity)
        adjacency[edge.from_component].add(edge.to_component)
        edges.append(
            {
                "from_component": edge.from_component,
                "to_component": edge.to_component,
                "relationship": edge.relationship.value,
            }
        )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise PackageGraphError("component graph must be acyclic")
        if node in visited:
            return
        visiting.add(node)
        for child in sorted(adjacency[node]):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(node_refs):
        visit(node)
    normalized = sorted(
        edges,
        key=lambda item: (item["from_component"], item["to_component"], item["relationship"]),
    )
    digest = _sha256(canonical_json_bytes({"nodes": sorted(node_refs), "edges": normalized}))
    return normalized, digest


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum() or character == "_")


def _require_safe_sbom_keys(document: dict[str, Any]) -> None:
    for key in _walk_keys(document):
        if _normalized_key(key) in _FORBIDDEN_SBOM_KEYS:
            raise PackageSbomError("SBOM contains a forbidden content-bearing field")


def _extract_cyclonedx(document: dict[str, Any], spec_version: str) -> tuple[int, set[str]]:
    if document.get("bomFormat") != "CycloneDX" or str(document.get("specVersion")) != spec_version:
        raise PackageSbomError("CycloneDX document format/specVersion does not match manifest")
    components = document.get("components")
    if not isinstance(components, list) or not components or len(components) > MAX_SBOM_COMPONENTS:
        raise PackageSbomError("CycloneDX components must contain 1..2048 entries")
    digests: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise PackageSbomError("CycloneDX components must be objects")
        hashes = component.get("hashes", [])
        if not isinstance(hashes, list):
            raise PackageSbomError("CycloneDX hashes must be an array")
        for item in hashes:
            if not isinstance(item, dict):
                continue
            algorithm = str(item.get("alg", "")).replace("_", "-").upper()
            digest = item.get("content")
            if algorithm == "SHA-256" and isinstance(digest, str) and len(digest) == 64:
                digests.add(digest.casefold())
    return len(components), digests


def _extract_spdx(document: dict[str, Any], spec_version: str) -> tuple[int, set[str]]:
    if str(document.get("spdxVersion")) != spec_version:
        raise PackageSbomError("SPDX document spdxVersion does not match manifest")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages or len(packages) > MAX_SBOM_COMPONENTS:
        raise PackageSbomError("SPDX packages must contain 1..2048 entries")
    digests: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            raise PackageSbomError("SPDX packages must be objects")
        checksums = package.get("checksums", [])
        if not isinstance(checksums, list):
            raise PackageSbomError("SPDX checksums must be an array")
        for item in checksums:
            if not isinstance(item, dict):
                continue
            algorithm = str(item.get("algorithm", "")).replace("-", "").upper()
            digest = item.get("checksumValue")
            if algorithm == "SHA256" and isinstance(digest, str) and len(digest) == 64:
                digests.add(digest.casefold())
    return len(packages), digests


def validate_sbom(
    manifest: AgentPackageManifestV2,
    document: dict[str, Any],
) -> tuple[bytes, int, set[str]]:
    if not isinstance(document, dict):
        raise PackageSbomError("SBOM document must be an object")
    _require_safe_sbom_keys(document)
    payload = canonical_json_bytes(document)
    if len(payload) > MAX_SBOM_BYTES:
        raise PackageSbomError("SBOM document exceeds 2 MiB")
    actual_digest = _sha256(payload)
    if actual_digest != manifest.sbom.document_sha256:
        raise PackageSbomError("SBOM document digest does not match manifest")
    if manifest.sbom.format == PackageSbomFormat.CYCLONEDX_JSON:
        count, digests = _extract_cyclonedx(document, manifest.sbom.spec_version)
    else:
        count, digests = _extract_spdx(document, manifest.sbom.spec_version)
    required = {artifact.sha256 for artifact in manifest.artifacts}
    missing = sorted(required - digests)
    if missing:
        raise PackageSbomError(f"SBOM is missing {len(missing)} manifest artifact digest(s)")
    return payload, count, digests


def _provenance_digest(manifest: AgentPackageManifestV2) -> str:
    return _sha256(canonical_json_bytes(manifest.provenance.model_dump(mode="json")))


async def create_signing_key(
    db: AsyncSession,
    *,
    request: PackageSigningKeyCreate,
    actor: User,
) -> PackageSigningKey:
    await require_active_namespace(db, request.namespace_id)
    _, canonical_pem, fingerprint = _canonical_public_key(request.public_key_pem)
    existing = (
        await db.execute(
            select(PackageSigningKey).where(
                PackageSigningKey.namespace_id == request.namespace_id,
                (PackageSigningKey.key_id == request.key_id)
                | (PackageSigningKey.public_key_fingerprint == fingerprint),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise PackageRegistryConflictError("signing key id or fingerprint already exists")
    row = PackageSigningKey(
        public_id=_public_id("pkey"),
        namespace_id=request.namespace_id,
        key_id=request.key_id,
        algorithm=PackageSignatureAlgorithm.ED25519,
        public_key_pem=canonical_pem,
        public_key_fingerprint=fingerprint,
        status=PackageSigningKeyStatus.ACTIVE,
        created_by_user_id=actor.id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise PackageRegistryConflictError("signing key id or fingerprint already exists") from exc
    await audit(
        db,
        user=actor,
        action="package.signing_key.registered",
        resource_type="package_signing_key",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "key_id": row.key_id,
            "algorithm": row.algorithm.value,
            "public_key_fingerprint": row.public_key_fingerprint,
        },
    )
    await enqueue_domain_event(db, build_package_signing_key_registered(row), guaranteed_new=True)
    return row


async def list_signing_keys(
    db: AsyncSession, *, namespace_id: int, limit: int = 100
) -> list[PackageSigningKey]:
    return list(
        (
            await db.execute(
                select(PackageSigningKey)
                .where(PackageSigningKey.namespace_id == namespace_id)
                .order_by(PackageSigningKey.created_at.desc(), PackageSigningKey.id.desc())
                .limit(limit)
            )
        ).scalars()
    )


async def get_signing_key(db: AsyncSession, public_id: str) -> PackageSigningKey:
    row = (
        await db.execute(select(PackageSigningKey).where(PackageSigningKey.public_id == public_id))
    ).scalar_one_or_none()
    if row is None:
        raise PackageRegistryNotFoundError("signing key was not found")
    return row


async def revoke_signing_key(
    db: AsyncSession, *, public_id: str, actor: User
) -> PackageSigningKey:
    row = (
        await db.execute(
            select(PackageSigningKey)
            .where(PackageSigningKey.public_id == public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise PackageRegistryNotFoundError("signing key was not found")
    if row.status != PackageSigningKeyStatus.ACTIVE:
        raise PackageRegistryConflictError("signing key is already revoked")
    row.status = PackageSigningKeyStatus.REVOKED
    row.revoked_by_user_id = actor.id
    row.revoked_at = datetime.now(timezone.utc)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="package.signing_key.revoked",
        resource_type="package_signing_key",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "key_id": row.key_id,
            "public_key_fingerprint": row.public_key_fingerprint,
        },
    )
    await enqueue_domain_event(db, build_package_signing_key_revoked(row), guaranteed_new=True)
    return row


async def rotate_signing_key(
    db: AsyncSession,
    *,
    public_id: str,
    request: PackageSigningKeyRotate,
    actor: User,
) -> PackageSigningKey:
    previous = await db.scalar(
        select(PackageSigningKey)
        .where(PackageSigningKey.public_id == public_id)
        .with_for_update()
    )
    if previous is None:
        raise PackageRegistryNotFoundError("signing key was not found")
    if previous.status != PackageSigningKeyStatus.ACTIVE:
        raise PackageRegistryConflictError("signing key is not active")
    existing_successor = await db.scalar(
        select(PackageSigningKey).where(PackageSigningKey.rotated_from_id == previous.id)
    )
    if existing_successor is not None:
        raise PackageRegistryConflictError("signing key already has a rotation successor")
    _, canonical_pem, fingerprint = _canonical_public_key(request.public_key_pem)
    duplicate = await db.scalar(
        select(PackageSigningKey).where(
            PackageSigningKey.namespace_id == previous.namespace_id,
            (PackageSigningKey.key_id == request.key_id)
            | (PackageSigningKey.public_key_fingerprint == fingerprint),
        )
    )
    if duplicate is not None:
        raise PackageRegistryConflictError("signing key id or fingerprint already exists")
    now = datetime.now(timezone.utc)
    successor = PackageSigningKey(
        public_id=_public_id("pkey"),
        namespace_id=previous.namespace_id,
        key_id=request.key_id,
        algorithm=PackageSignatureAlgorithm.ED25519,
        public_key_pem=canonical_pem,
        public_key_fingerprint=fingerprint,
        status=PackageSigningKeyStatus.ACTIVE,
        created_by_user_id=actor.id,
        rotated_from_id=previous.id,
        rotation_sequence=previous.rotation_sequence + 1,
        created_at=now,
    )
    previous.status = PackageSigningKeyStatus.REVOKED
    previous.revoked_by_user_id = actor.id
    previous.revoked_at = now
    db.add(successor)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise PackageRegistryConflictError(
            "signing key rotation conflicts with an existing key"
        ) from exc
    await audit(
        db,
        user=actor,
        action="package.signing_key.rotated",
        resource_type="package_signing_key",
        resource_id=successor.id,
        namespace_id=successor.namespace_id,
        details={
            "previous_public_id": previous.public_id,
            "successor_public_id": successor.public_id,
            "previous_fingerprint": previous.public_key_fingerprint,
            "successor_fingerprint": successor.public_key_fingerprint,
            "rotation_sequence": successor.rotation_sequence,
        },
    )
    await enqueue_domain_event(
        db, build_package_signing_key_registered(successor), guaranteed_new=True
    )
    await enqueue_domain_event(
        db, build_package_signing_key_revoked(previous), guaranteed_new=True
    )
    await enqueue_domain_event(
        db,
        build_package_signing_key_rotated(previous, successor),
        guaranteed_new=True,
    )
    return successor


async def create_package(
    db: AsyncSession, *, request: AgentPackageCreate, actor: User
) -> AgentPackage:
    namespace = await require_active_namespace(db, request.namespace_id)
    asset = await db.get(AIAsset, request.agent_asset_id)
    if asset is None:
        raise PackageRegistryReferenceError("Agent asset was not found")
    if asset.namespace_id != request.namespace_id:
        raise PackageRegistryReferenceError("Agent asset must belong to the Package Namespace")
    if asset.asset_type != AssetType.AGENT:
        raise PackageRegistryReferenceError("Package root asset must have type agent")
    existing = (
        await db.execute(
            select(AgentPackage).where(
                AgentPackage.namespace_id == request.namespace_id,
                AgentPackage.name == request.name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise PackageRegistryConflictError("Package name already exists in this Namespace")
    agent_ref = asset.external_id or f"asset:{asset.id}"
    row = AgentPackage(
        public_id=_public_id("pkg"),
        namespace_id=request.namespace_id,
        namespace_ref=namespace.name,
        name=request.name,
        description=request.description,
        agent_asset_id=asset.id,
        agent_asset_ref=agent_ref,
        status=AgentPackageStatus.ACTIVE,
        created_by_user_id=actor.id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise PackageRegistryConflictError("Package name already exists in this Namespace") from exc
    await audit(
        db,
        user=actor,
        action="package.created",
        resource_type="agent_package",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "name": row.name,
            "agent_asset_id": row.agent_asset_id,
            "agent_asset_ref": row.agent_asset_ref,
        },
    )
    await enqueue_domain_event(db, build_agent_package_created(row), guaranteed_new=True)
    return row


async def _package_query(db: AsyncSession, public_id: str) -> AgentPackage | None:
    return (
        await db.execute(
            select(AgentPackage)
            .options(selectinload(AgentPackage.versions))
            .where(AgentPackage.public_id == public_id)
        )
    ).scalar_one_or_none()


async def get_package(db: AsyncSession, public_id: str) -> AgentPackage:
    row = await _package_query(db, public_id)
    if row is None:
        raise PackageRegistryNotFoundError("Package was not found")
    return row


async def list_packages(
    db: AsyncSession, *, namespace_id: int, limit: int = 100
) -> list[AgentPackage]:
    return list(
        (
            await db.execute(
                select(AgentPackage)
                .options(selectinload(AgentPackage.versions))
                .where(AgentPackage.namespace_id == namespace_id)
                .order_by(AgentPackage.created_at.desc(), AgentPackage.id.desc())
                .limit(limit)
            )
        ).scalars()
    )


def to_package_out(row: AgentPackage) -> AgentPackageOut:
    return AgentPackageOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        namespace_ref=row.namespace_ref,
        name=row.name,
        description=row.description,
        agent_asset_id=row.agent_asset_id,
        agent_asset_ref=row.agent_asset_ref,
        status=row.status,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version_count=len(row.versions),
    )


def _version_options():
    return (
        selectinload(AgentPackageVersion.package),
        selectinload(AgentPackageVersion.signing_key),
        selectinload(AgentPackageVersion.components),
        selectinload(AgentPackageVersion.dependencies).selectinload(PackageDependency.from_component),
        selectinload(AgentPackageVersion.dependencies).selectinload(PackageDependency.to_component),
        selectinload(AgentPackageVersion.sbom),
    )


async def get_package_version(db: AsyncSession, public_id: str) -> AgentPackageVersion:
    row = (
        await db.execute(
            select(AgentPackageVersion)
            .options(*_version_options())
            .where(AgentPackageVersion.public_id == public_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise PackageRegistryNotFoundError("PackageVersion was not found")
    return row


async def list_package_versions(
    db: AsyncSession, *, package_public_id: str, limit: int = 100
) -> list[AgentPackageVersion]:
    package = await get_package(db, package_public_id)
    return list(
        (
            await db.execute(
                select(AgentPackageVersion)
                .options(*_version_options())
                .where(AgentPackageVersion.package_id == package.id)
                .order_by(AgentPackageVersion.created_at.desc(), AgentPackageVersion.id.desc())
                .limit(limit)
            )
        ).scalars()
    )


def _validate_manifest_identity(
    package: AgentPackage, request: AgentPackageVersionCreate
) -> None:
    manifest = request.manifest
    if request.namespace_id != package.namespace_id:
        raise PackageRegistryReferenceError("Package must belong to the requested Namespace")
    if manifest.package_id != package.public_id or request.package_public_id != package.public_id:
        raise PackageManifestError("manifest package_id must equal the selected Package public ID")
    if manifest.namespace_id != package.namespace_ref:
        raise PackageManifestError("manifest namespace_id must equal the frozen Package Namespace ref")
    if manifest.agent.asset_id != package.agent_asset_ref:
        raise PackageManifestError("manifest Agent asset ref must equal the frozen Package Agent ref")
    if manifest.package_version.casefold() in {"latest", "current", "newest"}:
        raise PackageManifestError("PackageVersion must be an exact semantic version")


async def _find_idempotent_version(
    db: AsyncSession, request: AgentPackageVersionCreate
) -> AgentPackageVersion | None:
    return (
        await db.execute(
            select(AgentPackageVersion)
            .options(*_version_options())
            .where(
                AgentPackageVersion.namespace_id == request.namespace_id,
                AgentPackageVersion.idempotency_key == request.idempotency_key,
            )
        )
    ).scalar_one_or_none()


def _matches_idempotent_evidence(
    replay: AgentPackageVersion,
    *,
    package_id: int,
    version: str,
    manifest_sha: str,
    signing_key_id: int,
    signature_sha: str,
    sbom_sha: str,
) -> bool:
    return (
        replay.package_id == package_id
        and replay.version == version
        and replay.manifest_digest == manifest_sha
        and replay.signing_key_id == signing_key_id
        and replay.signature_digest == signature_sha
        and replay.sbom.document_sha256 == sbom_sha
    )


async def _store_verified_sbom(
    storage: ArtifactStorageService,
    *,
    object_key: str,
    sbom_bytes: bytes,
    media_type: str,
    package_public_id: str,
    package_version: str,
    sbom_sha: str,
) -> None:
    """Write exact signed SBOM bytes to their content-addressed location.

    Exact idempotent replays deliberately repeat this write. Besides being
    harmless for a content-addressed object, it repairs a missing object after
    a process interruption between object storage and database commit.
    """

    try:
        await storage.put_object_bytes_async(
            object_key=object_key,
            payload=sbom_bytes,
            content_type=media_type,
            metadata={
                "package-public-id": package_public_id,
                "package-version": package_version,
                "sha256": sbom_sha,
            },
        )
    except Exception as exc:
        raise PackageStorageError("failed to store verified SBOM") from exc


async def register_package_version(
    db: AsyncSession,
    *,
    request: AgentPackageVersionCreate,
    actor: User,
    storage: ArtifactStorageService = artifact_service,
) -> AgentPackageVersion:
    package = await get_package(db, request.package_public_id)
    _validate_manifest_identity(package, request)
    if package.status != AgentPackageStatus.ACTIVE:
        raise PackageRegistryConflictError("Package is not active")
    key = await get_signing_key(db, request.signing_key_public_id)
    if key.namespace_id != package.namespace_id:
        raise PackageRegistryReferenceError("signing key must belong to the Package Namespace")
    if key.status != PackageSigningKeyStatus.ACTIVE:
        raise PackageSignatureError("signing key is revoked")

    manifest_bytes = canonical_manifest_bytes(request.manifest)
    manifest_sha = _sha256(manifest_bytes)
    signature_bytes = _verify_signature(
        key=key, signature_value=request.signature, payload=manifest_bytes
    )
    signature_sha = _sha256(signature_bytes)
    normalized_edges, graph_sha = _validate_graph(request.manifest)
    sbom_bytes, component_count, _ = validate_sbom(request.manifest, request.sbom_document)
    provenance_sha = _provenance_digest(request.manifest)
    object_key = storage.package_sbom_object_key(
        namespace_id=package.namespace_id,
        package_public_id=package.public_id,
        package_version=request.manifest.package_version,
        sha256=request.manifest.sbom.document_sha256,
    )

    replay = await _find_idempotent_version(db, request)
    if replay is not None:
        if _matches_idempotent_evidence(
            replay,
            package_id=package.id,
            version=request.manifest.package_version,
            manifest_sha=manifest_sha,
            signing_key_id=key.id,
            signature_sha=signature_sha,
            sbom_sha=request.manifest.sbom.document_sha256,
        ):
            await _store_verified_sbom(
                storage,
                object_key=object_key,
                sbom_bytes=sbom_bytes,
                media_type=request.manifest.sbom.media_type,
                package_public_id=package.public_id,
                package_version=request.manifest.package_version,
                sbom_sha=request.manifest.sbom.document_sha256,
            )
            return replay
        raise PackageRegistryConflictError("idempotency key is bound to different Package evidence")

    existing_version = (
        await db.execute(
            select(AgentPackageVersion.id).where(
                AgentPackageVersion.package_id == package.id,
                AgentPackageVersion.version == request.manifest.package_version,
            )
        )
    ).scalar_one_or_none()
    if existing_version is not None:
        raise PackageRegistryConflictError("Package semantic version already exists")

    await _store_verified_sbom(
        storage,
        object_key=object_key,
        sbom_bytes=sbom_bytes,
        media_type=request.manifest.sbom.media_type,
        package_public_id=package.public_id,
        package_version=request.manifest.package_version,
        sbom_sha=request.manifest.sbom.document_sha256,
    )

    now = datetime.now(timezone.utc)
    package_id = package.id
    signing_key_id = key.id
    row = AgentPackageVersion(
        public_id=_public_id("pkgv"),
        namespace_id=package.namespace_id,
        package_id=package.id,
        version=request.manifest.package_version,
        status=AgentPackageVersionStatus.VERIFIED,
        manifest_schema_version=request.manifest.schema_version,
        manifest_json=canonical_manifest_document(request.manifest),
        manifest_digest=manifest_sha,
        graph_digest=graph_sha,
        evaluation_policy_id=request.manifest.evaluation_policy_id,
        source_repository=request.manifest.provenance.source_repository,
        source_revision=request.manifest.provenance.source_revision,
        built_at=request.manifest.provenance.built_at,
        builder_id=request.manifest.provenance.builder_id,
        build_id=request.manifest.provenance.build_id,
        provenance_digest=provenance_sha,
        signing_key_id=key.id,
        signature_algorithm=key.algorithm,
        signature_value=request.signature,
        signature_digest=signature_sha,
        signature_verified_at=now,
        idempotency_key=request.idempotency_key,
        created_by_user_id=actor.id,
        components=[
            PackageComponent(
                position=index,
                component_ref=artifact.component_ref,
                component_type=artifact.type,
                asset_ref=artifact.asset_id,
                version_ref=artifact.version_id,
                uri=artifact.uri,
                sha256=artifact.sha256,
                media_type=artifact.media_type,
            )
            for index, artifact in enumerate(request.manifest.artifacts)
        ],
        dependencies=[],
        sbom=None,
    )
    db.add(row)
    try:
        await db.flush()
        component_by_ref = {component.component_ref: component for component in row.components}
        row.dependencies = [
            PackageDependency(
                package_version_id=row.id,
                from_component_id=component_by_ref[edge["from_component"]].id,
                to_component_id=component_by_ref[edge["to_component"]].id,
                relationship_kind=PackageDependencyRelationship(edge["relationship"]),
            )
            for edge in normalized_edges
        ]
        row.sbom = PackageSbom(
            public_id=_public_id("psbom"),
            package_version_id=row.id,
            format=request.manifest.sbom.format,
            spec_version=request.manifest.sbom.spec_version,
            media_type=request.manifest.sbom.media_type,
            document_sha256=request.manifest.sbom.document_sha256,
            object_key=object_key,
            size_bytes=len(sbom_bytes),
            component_count=component_count,
            stored_at=now,
        )
        await db.flush()
        await audit(
            db,
            user=actor,
            action="package.version.registered",
            resource_type="agent_package_version",
            resource_id=row.id,
            namespace_id=row.namespace_id,
            details={
                "public_id": row.public_id,
                "package_public_id": package.public_id,
                "version": row.version,
                "manifest_digest": row.manifest_digest,
                "graph_digest": row.graph_digest,
                "provenance_digest": row.provenance_digest,
                "signature_digest": row.signature_digest,
                "signing_key_fingerprint": key.public_key_fingerprint,
                "sbom_document_sha256": row.sbom.document_sha256,
                "component_count": len(row.components),
                "dependency_count": len(row.dependencies),
                "sbom_component_count": row.sbom.component_count,
            },
        )
        await enqueue_domain_event(
            db,
            build_agent_package_version_registered(row),
            guaranteed_new=True,
        )
    except IntegrityError as exc:
        # FastAPI finishes yield-dependency commits just after sending the
        # response. An immediate retry can therefore begin under MySQL's
        # REPEATABLE READ snapshot before the first commit is visible, then
        # lose the unique-key race at flush. Roll back that snapshot and
        # resolve the winner as an idempotent replay. The SBOM key is
        # content-addressed, so it must not be deleted here: another committed
        # PackageVersion may already own the exact object.
        await db.rollback()
        replay = await _find_idempotent_version(db, request)
        if replay is not None and _matches_idempotent_evidence(
            replay,
            package_id=package_id,
            version=request.manifest.package_version,
            manifest_sha=manifest_sha,
            signing_key_id=signing_key_id,
            signature_sha=signature_sha,
            sbom_sha=request.manifest.sbom.document_sha256,
        ):
            return replay
        raise PackageRegistryConflictError(
            "Package semantic version or idempotency key already exists"
        ) from exc
    return await get_package_version(db, row.public_id)


def to_package_version_out(row: AgentPackageVersion) -> AgentPackageVersionOut:
    return AgentPackageVersionOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        package_public_id=row.package.public_id,
        package_name=row.package.name,
        version=row.version,
        status=row.status,
        manifest_schema_version=row.manifest_schema_version,
        manifest=AgentPackageManifestV2.model_validate(row.manifest_json),
        manifest_digest=row.manifest_digest,
        graph_digest=row.graph_digest,
        evaluation_policy_id=row.evaluation_policy_id,
        provenance_digest=row.provenance_digest,
        signing_key_public_id=row.signing_key.public_id,
        signing_key_id=row.signing_key.key_id,
        signing_key_fingerprint=row.signing_key.public_key_fingerprint,
        signing_key_status=row.signing_key.status,
        signature_algorithm=row.signature_algorithm,
        signature=row.signature_value,
        signature_digest=row.signature_digest,
        signature_verified_at=row.signature_verified_at,
        idempotency_key=row.idempotency_key,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        components=[PackageComponentOut.model_validate(component) for component in row.components],
        dependencies=[
            PackageDependencyOut(
                from_component=edge.from_component.component_ref,
                to_component=edge.to_component.component_ref,
                relationship=edge.relationship_kind,
            )
            for edge in row.dependencies
        ],
        sbom=PackageSbomOut.model_validate(row.sbom),
    )


async def verify_package_version(
    db: AsyncSession,
    *,
    public_id: str,
    storage: ArtifactStorageService = artifact_service,
) -> AgentPackageVersionVerificationOut:
    row = await get_package_version(db, public_id)
    manifest = AgentPackageManifestV2.model_validate(row.manifest_json)
    canonical = canonical_manifest_bytes(manifest)
    actual_manifest_digest = _sha256(canonical)
    _, actual_graph_digest = _validate_graph(manifest)
    actual_provenance_digest = _provenance_digest(manifest)
    signature_valid = True
    try:
        signature = _verify_signature(
            key=row.signing_key,
            signature_value=row.signature_value,
            payload=canonical,
        )
        signature_valid = _sha256(signature) == row.signature_digest
    except PackageSignatureError:
        signature_valid = False

    try:
        sbom_bytes = await storage.read_object_bytes_async(row.sbom.object_key, max_bytes=MAX_SBOM_BYTES)
        sbom_digest_valid = _sha256(sbom_bytes) == row.sbom.document_sha256
        document = json.loads(sbom_bytes.decode("utf-8"))
        _, _, digests = validate_sbom(manifest, document)
        sbom_component_coverage_valid = {
            artifact.sha256 for artifact in manifest.artifacts
        }.issubset(digests)
    except Exception:
        sbom_digest_valid = False
        sbom_component_coverage_valid = False

    verified = all(
        (
            actual_manifest_digest == row.manifest_digest,
            actual_graph_digest == row.graph_digest,
            actual_provenance_digest == row.provenance_digest,
            signature_valid,
            sbom_digest_valid,
            sbom_component_coverage_valid,
        )
    )
    return AgentPackageVersionVerificationOut(
        package_version_public_id=row.public_id,
        manifest_digest=row.manifest_digest,
        graph_digest=row.graph_digest,
        provenance_digest=row.provenance_digest,
        signature_valid=signature_valid,
        signature_verified_at=row.signature_verified_at,
        signing_key_status=row.signing_key.status,
        signing_key_fingerprint=row.signing_key.public_key_fingerprint,
        sbom_digest_valid=sbom_digest_valid,
        sbom_component_coverage_valid=sbom_component_coverage_valid,
        sbom_document_sha256=row.sbom.document_sha256,
        verified=verified,
    )


async def package_sbom_download(
    db: AsyncSession,
    *,
    public_id: str,
    storage: ArtifactStorageService = artifact_service,
) -> PackageSbomDownloadOut:
    row = await get_package_version(db, public_id)
    try:
        signed = storage.generate_presigned_get_url(object_key=row.sbom.object_key)
    except Exception as exc:
        raise PackageStorageError("failed to issue SBOM download URL") from exc
    return PackageSbomDownloadOut(
        package_version_public_id=row.public_id,
        sbom_public_id=row.sbom.public_id,
        document_sha256=row.sbom.document_sha256,
        **signed,
    )


async def package_count(db: AsyncSession, namespace_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(AgentPackage.id)).where(AgentPackage.namespace_id == namespace_id)
            )
        ).scalar_one()
    )
