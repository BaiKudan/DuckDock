from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import (
    CurrentUser,
    DB,
    require_namespace_admin,
    require_namespace_member,
    require_namespace_writer,
)
from app.schemas.package_registry import (
    AgentPackageCreate,
    AgentPackageManifestV2,
    AgentPackageOut,
    AgentPackageVersionCreate,
    AgentPackageVersionOut,
    AgentPackageVersionVerificationOut,
    PackageSbomDownloadOut,
    PackageSigningKeyCreate,
    PackageSigningKeyOut,
    PackageSigningKeyRotate,
)
from app.services.package_registry_service import (
    PackageManifestError,
    PackageRegistryConflictError,
    PackageRegistryNotFoundError,
    PackageRegistryReferenceError,
    PackageSbomError,
    PackageSignatureError,
    PackageStorageError,
    canonical_manifest_bytes,
    create_package,
    create_signing_key,
    get_package,
    get_package_version,
    get_signing_key,
    list_package_versions,
    list_packages,
    list_signing_keys,
    manifest_digest,
    package_sbom_download,
    register_package_version,
    rotate_signing_key,
    revoke_signing_key,
    to_package_out,
    to_package_version_out,
    verify_package_version,
)


package_router = APIRouter(prefix="/agent-packages", tags=["agent-package-registry"])
version_router = APIRouter(prefix="/agent-package-versions", tags=["agent-package-registry"])
signing_key_router = APIRouter(prefix="/package-signing-keys", tags=["agent-package-registry"])
manifest_router = APIRouter(prefix="/agent-package-manifests", tags=["agent-package-registry"])


def _http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, PackageRegistryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PackageRegistryConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PackageStorageError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(
        exc,
        (
            PackageRegistryReferenceError,
            PackageManifestError,
            PackageSbomError,
            PackageSignatureError,
        ),
    ):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail="Package Registry operation rejected")


async def _require_visible_namespace(current_user, namespace_id: int, db: DB) -> None:
    try:
        await require_namespace_member(current_user, namespace_id, db)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=404, detail="Package Registry resource not found") from exc
        raise


@package_router.post("", response_model=AgentPackageOut, status_code=status.HTTP_201_CREATED)
async def create_agent_package(body: AgentPackageCreate, db: DB, current_user: CurrentUser):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        row = await create_package(db, request=body, actor=current_user)
        row = await get_package(db, row.public_id)
        return to_package_out(row)
    except Exception as exc:
        if isinstance(exc, (PackageRegistryConflictError, PackageRegistryReferenceError)):
            raise _http_exception(exc) from exc
        raise


@package_router.get("", response_model=list[AgentPackageOut])
async def list_agent_packages(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_packages(db, namespace_id=namespace_id, limit=limit)
    return [to_package_out(row) for row in rows]


@package_router.get("/{public_id}", response_model=AgentPackageOut)
async def get_agent_package(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_package(db, public_id)
        await _require_visible_namespace(current_user, row.namespace_id, db)
        return to_package_out(row)
    except PackageRegistryNotFoundError as exc:
        raise _http_exception(exc) from exc


@package_router.get("/{public_id}/versions", response_model=list[AgentPackageVersionOut])
async def list_agent_package_versions(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    try:
        package = await get_package(db, public_id)
        await _require_visible_namespace(current_user, package.namespace_id, db)
        rows = await list_package_versions(db, package_public_id=public_id, limit=limit)
        return [to_package_version_out(row) for row in rows]
    except PackageRegistryNotFoundError as exc:
        raise _http_exception(exc) from exc


@version_router.post("", response_model=AgentPackageVersionOut, status_code=status.HTTP_201_CREATED)
async def create_agent_package_version(
    body: AgentPackageVersionCreate, db: DB, current_user: CurrentUser
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        row = await register_package_version(db, request=body, actor=current_user)
        return to_package_version_out(row)
    except (
        PackageRegistryNotFoundError,
        PackageRegistryConflictError,
        PackageRegistryReferenceError,
        PackageManifestError,
        PackageSbomError,
        PackageSignatureError,
        PackageStorageError,
    ) as exc:
        raise _http_exception(exc) from exc


@version_router.get("/{public_id}", response_model=AgentPackageVersionOut)
async def get_agent_package_version(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_package_version(db, public_id)
        await _require_visible_namespace(current_user, row.namespace_id, db)
        return to_package_version_out(row)
    except PackageRegistryNotFoundError as exc:
        raise _http_exception(exc) from exc


@version_router.post("/{public_id}/verify", response_model=AgentPackageVersionVerificationOut)
async def verify_agent_package_version(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_package_version(db, public_id)
        await _require_visible_namespace(current_user, row.namespace_id, db)
        return await verify_package_version(db, public_id=public_id)
    except (PackageRegistryNotFoundError, PackageStorageError) as exc:
        raise _http_exception(exc) from exc


@version_router.get("/{public_id}/sbom/download", response_model=PackageSbomDownloadOut)
async def download_agent_package_sbom(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_package_version(db, public_id)
        await _require_visible_namespace(current_user, row.namespace_id, db)
        return await package_sbom_download(db, public_id=public_id)
    except (PackageRegistryNotFoundError, PackageStorageError) as exc:
        raise _http_exception(exc) from exc


@signing_key_router.post(
    "", response_model=PackageSigningKeyOut, status_code=status.HTTP_201_CREATED
)
async def create_package_signing_key(
    body: PackageSigningKeyCreate, db: DB, current_user: CurrentUser
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        return await create_signing_key(db, request=body, actor=current_user)
    except (PackageRegistryConflictError, PackageSignatureError) as exc:
        raise _http_exception(exc) from exc


@signing_key_router.get("", response_model=list[PackageSigningKeyOut])
async def list_package_signing_keys(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    return await list_signing_keys(db, namespace_id=namespace_id, limit=limit)


@signing_key_router.post("/{public_id}/revoke", response_model=PackageSigningKeyOut)
async def revoke_package_signing_key(
    public_id: str, db: DB, current_user: CurrentUser
):
    try:
        row = await get_signing_key(db, public_id)
        await require_namespace_admin(current_user, row.namespace_id, db)
        return await revoke_signing_key(db, public_id=public_id, actor=current_user)
    except (PackageRegistryNotFoundError, PackageRegistryConflictError) as exc:
        raise _http_exception(exc) from exc


@signing_key_router.post("/{public_id}/rotate", response_model=PackageSigningKeyOut)
async def rotate_package_signing_key(
    public_id: str,
    body: PackageSigningKeyRotate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        row = await get_signing_key(db, public_id)
        await require_namespace_admin(current_user, row.namespace_id, db)
        return await rotate_signing_key(
            db, public_id=public_id, request=body, actor=current_user
        )
    except (
        PackageRegistryNotFoundError,
        PackageRegistryConflictError,
        PackageSignatureError,
    ) as exc:
        raise _http_exception(exc) from exc


@manifest_router.post("/canonicalize")
async def canonicalize_agent_package_manifest(
    namespace_id: int,
    body: AgentPackageManifestV2,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_member(current_user, namespace_id, db)
    canonical = canonical_manifest_bytes(body)
    return {
        "schema_version": body.schema_version,
        "manifest_digest": manifest_digest(body),
        "canonical_manifest": canonical.decode("utf-8"),
        "size_bytes": len(canonical),
    }
