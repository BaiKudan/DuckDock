from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import CurrentUser, DB, require_namespace_member, require_namespace_writer
from app.models.control_plane import HandoverCase
from app.schemas.handover import (
    HandoverAcceptanceCreate,
    HandoverAcceptanceOut,
    HandoverEvidenceSnapshotCreate,
    HandoverEvidenceSnapshotOut,
    HandoverObligationOut,
    HandoverObligationReceiptCreate,
    HandoverObligationReceiptOut,
    HandoverSignedPackageCreate,
    HandoverSignedPackageOut,
    HandoverSigningPayloadOut,
)
from app.services.handover_evidence_service import (
    HandoverEvidenceConflictError,
    HandoverEvidenceError,
    HandoverEvidenceNotFoundError,
    HandoverEvidenceReferenceError,
    HandoverEvidenceStateError,
    HandoverEvidenceTenantMismatchError,
    create_handover_acceptance,
    create_handover_signed_package,
    create_handover_snapshot,
    get_handover_acceptance,
    get_handover_obligation,
    get_handover_signed_package,
    get_handover_snapshot,
    handover_acceptance_out,
    handover_obligation_out,
    handover_obligation_receipt_out,
    handover_signed_package_out,
    handover_signing_payload,
    handover_snapshot_out,
    list_handover_acceptances,
    list_handover_signed_packages,
    list_handover_snapshots,
    record_handover_obligation_receipt,
)


snapshot_router = APIRouter(prefix="/handover-evidence-snapshots", tags=["handover-2"])
obligation_router = APIRouter(prefix="/handover-obligations", tags=["handover-2"])
acceptance_router = APIRouter(prefix="/handover-acceptances", tags=["handover-2"])
package_router = APIRouter(prefix="/handover-signed-packages", tags=["handover-2"])


def _http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, HandoverEvidenceNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, HandoverEvidenceConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, HandoverEvidenceTenantMismatchError):
        return HTTPException(status_code=404, detail="Handover resource was not found")
    if isinstance(exc, (HandoverEvidenceReferenceError, HandoverEvidenceStateError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail="Handover operation rejected")


async def _visible(current_user, namespace_id: int, db: DB) -> None:
    try:
        await require_namespace_member(current_user, namespace_id, db)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=404, detail="Handover resource was not found") from exc
        raise


@snapshot_router.post("", response_model=HandoverEvidenceSnapshotOut, status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    body: HandoverEvidenceSnapshotCreate,
    db: DB,
    current_user: CurrentUser,
):
    case = await db.get(HandoverCase, body.handover_case_id)
    if case is None or case.namespace_id is None:
        raise HTTPException(status_code=404, detail="Handover case was not found")
    await require_namespace_writer(current_user, case.namespace_id, db)
    try:
        return handover_snapshot_out(
            await create_handover_snapshot(db, request=body, actor=current_user)
        )
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc


@snapshot_router.get("", response_model=list[HandoverEvidenceSnapshotOut])
async def list_snapshots(
    db: DB,
    current_user: CurrentUser,
    namespace_id: int = Query(ge=1),
    handover_case_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
):
    await _visible(current_user, namespace_id, db)
    rows = await list_handover_snapshots(
        db,
        namespace_id=namespace_id,
        handover_case_id=handover_case_id,
        limit=limit,
    )
    return [handover_snapshot_out(row) for row in rows]


@snapshot_router.get("/{public_id}", response_model=HandoverEvidenceSnapshotOut)
async def get_snapshot(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_handover_snapshot(db, public_id)
        await _visible(current_user, row.namespace_id, db)
        return handover_snapshot_out(row)
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc


@obligation_router.get("/{public_id}", response_model=HandoverObligationOut)
async def get_obligation(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_handover_obligation(db, public_id)
        await _visible(current_user, row.namespace_id, db)
        return handover_obligation_out(row)
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc


@obligation_router.post(
    "/{public_id}/receipts",
    response_model=HandoverObligationReceiptOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_obligation_receipt(
    public_id: str,
    body: HandoverObligationReceiptCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        obligation = await get_handover_obligation(db, public_id)
        await require_namespace_writer(current_user, obligation.namespace_id, db)
        row = await record_handover_obligation_receipt(
            db,
            obligation_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return handover_obligation_receipt_out(row)
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc


@acceptance_router.post("", response_model=HandoverAcceptanceOut, status_code=status.HTTP_201_CREATED)
async def create_acceptance(
    body: HandoverAcceptanceCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        snapshot = await get_handover_snapshot(db, body.snapshot_public_id)
        await _visible(current_user, snapshot.namespace_id, db)
        return handover_acceptance_out(
            await create_handover_acceptance(db, request=body, actor=current_user)
        )
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc


@acceptance_router.get("", response_model=list[HandoverAcceptanceOut])
async def list_acceptances(
    db: DB,
    current_user: CurrentUser,
    namespace_id: int = Query(ge=1),
    handover_case_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
):
    await _visible(current_user, namespace_id, db)
    rows = await list_handover_acceptances(
        db,
        namespace_id=namespace_id,
        handover_case_id=handover_case_id,
        limit=limit,
    )
    return [handover_acceptance_out(row) for row in rows]


@acceptance_router.get(
    "/{public_id}/signing-payload",
    response_model=HandoverSigningPayloadOut,
)
async def get_signing_payload(public_id: str, db: DB, current_user: CurrentUser):
    try:
        acceptance = await get_handover_acceptance(db, public_id)
        await _visible(current_user, acceptance.namespace_id, db)
        return await handover_signing_payload(db, acceptance_public_id=public_id)
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc


@package_router.post("", response_model=HandoverSignedPackageOut, status_code=status.HTTP_201_CREATED)
async def create_signed_package(
    body: HandoverSignedPackageCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        acceptance = await get_handover_acceptance(db, body.acceptance_public_id)
        await require_namespace_writer(current_user, acceptance.namespace_id, db)
        return handover_signed_package_out(
            await create_handover_signed_package(db, request=body, actor=current_user)
        )
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc


@package_router.get("", response_model=list[HandoverSignedPackageOut])
async def list_signed_packages(
    db: DB,
    current_user: CurrentUser,
    namespace_id: int = Query(ge=1),
    handover_case_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
):
    await _visible(current_user, namespace_id, db)
    rows = await list_handover_signed_packages(
        db,
        namespace_id=namespace_id,
        handover_case_id=handover_case_id,
        limit=limit,
    )
    return [handover_signed_package_out(row) for row in rows]


@package_router.get("/{public_id}", response_model=HandoverSignedPackageOut)
async def get_signed_package(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_handover_signed_package(db, public_id)
        await _visible(current_user, row.namespace_id, db)
        return handover_signed_package_out(row)
    except HandoverEvidenceError as exc:
        raise _http_exception(exc) from exc
