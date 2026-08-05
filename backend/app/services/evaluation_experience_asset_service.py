from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.evaluation import (
    EvaluationExperienceActivationDecision,
    EvaluationExperienceActivationRequest,
    EvaluationExperienceActivationReview,
    EvaluationExperienceAsset,
    EvaluationExperienceAssetVersion,
    EvaluationExperienceAssetVersionStatus,
    EvaluationExperienceCandidate,
    EvaluationExperienceCandidateStatus,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationExperienceActivationRequestCreate,
    EvaluationExperienceActivationReviewCreate,
    EvaluationExperienceAssetCreate,
    EvaluationExperienceAssetVersionCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)
from app.services.outbox_event_service import (
    build_evaluation_experience_activation_requested,
    build_evaluation_experience_activation_reviewed,
    build_evaluation_experience_asset_version_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_VERSION_SCHEMA_NAME = "duckdock-experience-asset-version"
_VERSION_SCHEMA_VERSION = "1.0"


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utcnow_mysql_safe() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _asset_options():
    return (
        selectinload(EvaluationExperienceAsset.source_candidate).selectinload(
            EvaluationExperienceCandidate.run
        ),
        selectinload(EvaluationExperienceAsset.versions)
        .selectinload(EvaluationExperienceAssetVersion.activation_request)
        .selectinload(EvaluationExperienceActivationRequest.review),
    )


async def get_evaluation_experience_asset(
    db: AsyncSession,
    *,
    public_id: str,
    for_update: bool = False,
) -> EvaluationExperienceAsset:
    statement = (
        select(EvaluationExperienceAsset)
        .options(*_asset_options())
        .where(EvaluationExperienceAsset.public_id == public_id)
    )
    if for_update:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    asset = await db.scalar(statement)
    if asset is None:
        raise EvaluationHubNotFoundError("EvaluationExperienceAsset not found")
    return asset


async def list_evaluation_experience_assets(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationExperienceAsset]:
    rows = await db.scalars(
        select(EvaluationExperienceAsset)
        .options(*_asset_options())
        .where(EvaluationExperienceAsset.namespace_id == namespace_id)
        .order_by(EvaluationExperienceAsset.created_at.desc())
        .limit(limit)
    )
    return list(rows.unique())


async def create_evaluation_experience_asset(
    db: AsyncSession,
    *,
    request: EvaluationExperienceAssetCreate,
    actor: User,
) -> EvaluationExperienceAsset:
    await require_active_namespace(db, request.namespace_id)
    candidate = await db.scalar(
        select(EvaluationExperienceCandidate)
        .options(selectinload(EvaluationExperienceCandidate.run))
        .where(
            EvaluationExperienceCandidate.public_id
            == request.source_candidate_public_id
        )
        .with_for_update()
    )
    if candidate is None:
        raise EvaluationHubNotFoundError("EvaluationExperienceCandidate not found")
    if candidate.run.namespace_id != request.namespace_id:
        raise EvaluationHubNotFoundError("EvaluationExperienceCandidate not found")
    if candidate.status != EvaluationExperienceCandidateStatus.APPROVED:
        raise EvaluationHubStateError(
            "Experience asset requires an approved source candidate"
        )
    existing = await db.scalar(
        select(EvaluationExperienceAsset.id).where(
            (EvaluationExperienceAsset.source_candidate_id == candidate.id)
            | (
                (EvaluationExperienceAsset.namespace_id == request.namespace_id)
                & (EvaluationExperienceAsset.name == request.name)
            )
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "Experience asset already exists for the candidate or name"
        )
    now = _utcnow_mysql_safe()
    asset = EvaluationExperienceAsset(
        public_id=_public_id("eea"),
        namespace_id=request.namespace_id,
        source_candidate_id=candidate.id,
        name=request.name,
        description=request.description,
        created_by_user_id=actor.id,
        created_at=now,
    )
    asset.source_candidate = candidate
    asset.versions = []
    db.add(asset)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_experience_asset.created",
        resource_type="evaluation_experience_asset",
        resource_id=asset.id,
        namespace_id=asset.namespace_id,
        details={
            "public_id": asset.public_id,
            "source_candidate_public_id": candidate.public_id,
            "source_evidence_digest": candidate.evidence_digest,
        },
    )
    return asset


async def create_evaluation_experience_asset_version(
    db: AsyncSession,
    *,
    asset_public_id: str,
    request: EvaluationExperienceAssetVersionCreate,
    actor: User,
) -> EvaluationExperienceAsset:
    asset = await get_evaluation_experience_asset(
        db, public_id=asset_public_id, for_update=True
    )
    await require_active_namespace(db, asset.namespace_id)
    content_digest = _digest(
        {
            "body": request.body,
            "applicability": request.applicability,
            "change_summary": request.change_summary,
            "schema_name": _VERSION_SCHEMA_NAME,
            "schema_version": _VERSION_SCHEMA_VERSION,
        }
    )
    duplicate = await db.scalar(
        select(EvaluationExperienceAssetVersion.id).where(
            EvaluationExperienceAssetVersion.asset_id == asset.id,
            EvaluationExperienceAssetVersion.content_digest == content_digest,
        )
    )
    if duplicate is not None:
        raise EvaluationHubConflictError(
            "An identical Experience asset version already exists"
        )
    latest_version = await db.scalar(
        select(func.max(EvaluationExperienceAssetVersion.version)).where(
            EvaluationExperienceAssetVersion.asset_id == asset.id
        )
    )
    now = _utcnow_mysql_safe()
    version = EvaluationExperienceAssetVersion(
        public_id=_public_id("eeav"),
        asset_id=asset.id,
        version=int(latest_version or 0) + 1,
        status=EvaluationExperienceAssetVersionStatus.DRAFT,
        body=request.body,
        applicability=request.applicability,
        change_summary=request.change_summary,
        content_digest=content_digest,
        source_evidence_digest=asset.source_candidate.evidence_digest,
        schema_name=_VERSION_SCHEMA_NAME,
        schema_version=_VERSION_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=now,
    )
    asset.versions.append(version)
    db.add(version)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_experience_asset_version_created(asset, version),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_experience_asset_version.created",
        resource_type="evaluation_experience_asset_version",
        resource_id=version.id,
        namespace_id=asset.namespace_id,
        details={
            "asset_public_id": asset.public_id,
            "version_public_id": version.public_id,
            "version": version.version,
            "content_digest": version.content_digest,
            "source_evidence_digest": version.source_evidence_digest,
        },
    )
    return await get_evaluation_experience_asset(db, public_id=asset.public_id)


async def get_evaluation_experience_asset_version(
    db: AsyncSession,
    *,
    public_id: str,
    for_update: bool = False,
) -> EvaluationExperienceAssetVersion:
    statement = (
        select(EvaluationExperienceAssetVersion)
        .options(
            selectinload(EvaluationExperienceAssetVersion.asset).selectinload(
                EvaluationExperienceAsset.source_candidate
            ),
            selectinload(EvaluationExperienceAssetVersion.activation_request).selectinload(
                EvaluationExperienceActivationRequest.review
            ),
        )
        .where(EvaluationExperienceAssetVersion.public_id == public_id)
    )
    if for_update:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    version = await db.scalar(statement)
    if version is None:
        raise EvaluationHubNotFoundError(
            "EvaluationExperienceAssetVersion not found"
        )
    return version


async def request_evaluation_experience_activation(
    db: AsyncSession,
    *,
    version_public_id: str,
    request: EvaluationExperienceActivationRequestCreate,
    actor: User,
) -> EvaluationExperienceAsset:
    version = await get_evaluation_experience_asset_version(
        db, public_id=version_public_id, for_update=True
    )
    asset = version.asset
    await require_active_namespace(db, asset.namespace_id)
    if version.status != EvaluationExperienceAssetVersionStatus.DRAFT:
        raise EvaluationHubConflictError(
            "Only a draft Experience version can request activation"
        )
    now = _utcnow_mysql_safe()
    request_digest = _digest(
        {
            "asset_public_id": asset.public_id,
            "version_public_id": version.public_id,
            "content_digest": version.content_digest,
            "request_note_digest": (
                _digest({"request_note": request.request_note})
                if request.request_note is not None
                else None
            ),
            "requested_by_user_id": actor.id,
            "created_at": now.isoformat(),
        }
    )
    activation_request = EvaluationExperienceActivationRequest(
        public_id=_public_id("eear"),
        namespace_id=asset.namespace_id,
        version_id=version.id,
        request_note=request.request_note,
        request_digest=request_digest,
        requested_by_user_id=actor.id,
        created_at=now,
    )
    version.status = EvaluationExperienceAssetVersionStatus.PENDING_ACTIVATION
    version.activation_request = activation_request
    db.add(activation_request)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_experience_activation_requested(version),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_experience_activation.requested",
        resource_type="evaluation_experience_activation_request",
        resource_id=activation_request.id,
        namespace_id=asset.namespace_id,
        details={
            "asset_public_id": asset.public_id,
            "version_public_id": version.public_id,
            "content_digest": version.content_digest,
            "request_digest": activation_request.request_digest,
        },
    )
    return await get_evaluation_experience_asset(db, public_id=asset.public_id)


async def get_evaluation_experience_activation_request(
    db: AsyncSession,
    *,
    public_id: str,
    for_update: bool = False,
) -> EvaluationExperienceActivationRequest:
    statement = (
        select(EvaluationExperienceActivationRequest)
        .options(
            selectinload(EvaluationExperienceActivationRequest.version_record)
            .selectinload(EvaluationExperienceAssetVersion.asset)
            .selectinload(EvaluationExperienceAsset.source_candidate),
            selectinload(EvaluationExperienceActivationRequest.review),
        )
        .where(EvaluationExperienceActivationRequest.public_id == public_id)
    )
    if for_update:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    activation_request = await db.scalar(statement)
    if activation_request is None:
        raise EvaluationHubNotFoundError(
            "EvaluationExperienceActivationRequest not found"
        )
    return activation_request


async def review_evaluation_experience_activation(
    db: AsyncSession,
    *,
    request_public_id: str,
    request: EvaluationExperienceActivationReviewCreate,
    actor: User,
) -> EvaluationExperienceAsset:
    activation_request = await get_evaluation_experience_activation_request(
        db, public_id=request_public_id, for_update=True
    )
    version = activation_request.version_record
    asset = version.asset
    version.activation_request = activation_request
    await require_active_namespace(db, asset.namespace_id)
    if activation_request.review is not None:
        raise EvaluationHubConflictError("Activation request already has a final review")
    if version.status != EvaluationExperienceAssetVersionStatus.PENDING_ACTIVATION:
        raise EvaluationHubConflictError("Experience version is not pending activation")
    if actor.id in {
        version.created_by_user_id,
        activation_request.requested_by_user_id,
    }:
        raise EvaluationHubStateError(
            "Activation reviewer must be independent from the version author and requester"
        )
    now = _utcnow_mysql_safe()
    review_digest = _digest(
        {
            "activation_request_public_id": activation_request.public_id,
            "request_digest": activation_request.request_digest,
            "decision": request.decision.value,
            "comment_digest": (
                _digest({"comment": request.comment})
                if request.comment is not None
                else None
            ),
            "reviewed_by_user_id": actor.id,
            "created_at": now.isoformat(),
        }
    )
    review = EvaluationExperienceActivationReview(
        public_id=_public_id("eearv"),
        activation_request_id=activation_request.id,
        decision=request.decision,
        comment=request.comment,
        review_digest=review_digest,
        reviewed_by_user_id=actor.id,
        created_at=now,
    )
    activation_request.review = review
    db.add(review)
    if request.decision == EvaluationExperienceActivationDecision.APPROVED:
        active_versions = await db.scalars(
            select(EvaluationExperienceAssetVersion).where(
                EvaluationExperienceAssetVersion.asset_id == asset.id,
                EvaluationExperienceAssetVersion.status
                == EvaluationExperienceAssetVersionStatus.ACTIVE,
                EvaluationExperienceAssetVersion.id != version.id,
            )
        )
        for active_version in active_versions:
            active_version.status = EvaluationExperienceAssetVersionStatus.RETIRED
        version.status = EvaluationExperienceAssetVersionStatus.ACTIVE
    else:
        version.status = EvaluationExperienceAssetVersionStatus.REJECTED
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_experience_activation_reviewed(version),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_experience_activation.reviewed",
        resource_type="evaluation_experience_activation_review",
        resource_id=review.id,
        namespace_id=asset.namespace_id,
        details={
            "asset_public_id": asset.public_id,
            "version_public_id": version.public_id,
            "decision": review.decision.value,
            "content_digest": version.content_digest,
            "request_digest": activation_request.request_digest,
            "review_digest": review.review_digest,
        },
    )
    return await get_evaluation_experience_asset(db, public_id=asset.public_id)
