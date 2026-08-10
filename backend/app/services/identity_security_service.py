from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_token_security import (
    hash_reporter_token_secret,
    verify_reporter_token_secret,
)
from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    Criticality,
    HandoverCase,
    HandoverCaseType,
    HandoverStatus,
    ReporterCredential,
    RuntimeInstance,
    RuntimeReportToken,
    WorkloadIdentityKind,
)
from app.models.iam import (
    DirectoryCredential,
    DirectoryLifecycleAction,
    DirectoryLifecycleEvent,
    DirectoryLifecycleSource,
    EmploymentStatus,
    IdentityLink,
    RoleBinding,
    SSOProviderConfig,
    UserHandoverProfile,
    directory_event_public_id,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.schemas.identity_security import (
    DirectoryCredentialCreate,
    WorkloadIdentityCreate,
)
from app.services.audit_service import audit
from app.services.outbox_event_service import (
    build_directory_user_disabled,
    build_workload_identity_issued,
    build_workload_identity_revoked,
    build_workload_identity_rotated,
    enqueue_domain_event,
)


SCIM_USERS_WRITE_SCOPE = "scim.users.write"
ALLOWED_WORKLOAD_SCOPES = frozenset(
    {
        "execution.write",
        "release.receipt",
        "report.heartbeat",
        "report.structured",
        "report.upload",
    }
)
_SAFE_DIRECTORY_EVENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class IdentitySecurityError(ValueError):
    pass


class IdentitySecurityNotFoundError(IdentitySecurityError):
    pass


class IdentitySecurityConflictError(IdentitySecurityError):
    pass


class IdentitySecurityStateError(IdentitySecurityError):
    pass


class IdentitySecurityCredentialError(IdentitySecurityError):
    pass


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _token(prefix_kind: str) -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return prefix, secret, f"dkr_{prefix_kind}_{prefix}_{secret}"


def _ensure_future(value: datetime | None, *, now: datetime) -> None:
    if value is not None and _utc(value) <= now:
        raise IdentitySecurityStateError("credential expiry must be in the future")


async def create_directory_credential(
    db: AsyncSession,
    *,
    request: DirectoryCredentialCreate,
    actor: User,
) -> tuple[DirectoryCredential, str]:
    now = _utc()
    _ensure_future(request.expires_at, now=now)
    provider = await db.scalar(
        select(SSOProviderConfig).where(SSOProviderConfig.id == request.provider_id)
    )
    if provider is None:
        raise IdentitySecurityNotFoundError("SSO provider was not found")
    if not provider.enabled:
        raise IdentitySecurityStateError("SSO provider must be enabled")
    prefix, secret, token = _token("scim")
    row = DirectoryCredential(
        provider_id=provider.id,
        name=request.name.strip(),
        token_prefix=prefix,
        token_hash=hash_reporter_token_secret(secret),
        scopes_json=[SCIM_USERS_WRITE_SCOPE],
        is_active=True,
        expires_at=request.expires_at,
        created_by_user_id=actor.id,
        created_at=now,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="identity.directory_credential.created",
        resource_type="directory_credential",
        resource_id=row.id,
        details={
            "provider_id": row.provider_id,
            "public_id": row.public_id,
            "scope_count": len(row.scopes_json),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
    )
    return row, token


async def list_directory_credentials(
    db: AsyncSession, *, provider_id: int | None = None
) -> list[DirectoryCredential]:
    stmt = select(DirectoryCredential).order_by(
        DirectoryCredential.created_at.desc(), DirectoryCredential.id.desc()
    )
    if provider_id is not None:
        stmt = stmt.where(DirectoryCredential.provider_id == provider_id)
    return list((await db.scalars(stmt.limit(200))).all())


async def revoke_directory_credential(
    db: AsyncSession,
    *,
    public_id: str,
    reason: str,
    actor: User,
) -> DirectoryCredential:
    row = await db.scalar(
        select(DirectoryCredential)
        .where(DirectoryCredential.public_id == public_id)
        .with_for_update()
    )
    if row is None:
        raise IdentitySecurityNotFoundError("directory credential was not found")
    if not row.is_active:
        raise IdentitySecurityConflictError("directory credential is already revoked")
    row.is_active = False
    row.revoked_at = _utc()
    row.revoked_by_user_id = actor.id
    await db.flush()
    await audit(
        db,
        user=actor,
        action="identity.directory_credential.revoked",
        resource_type="directory_credential",
        resource_id=row.id,
        details={
            "provider_id": row.provider_id,
            "public_id": row.public_id,
            "reason_code": "operator_revoked",
            "reason_length": len(reason),
        },
    )
    return row


async def authenticate_directory_credential(
    db: AsyncSession, token: str
) -> DirectoryCredential:
    parts = token.split("_", 3)
    if len(parts) != 4 or parts[0:2] != ["dkr", "scim"]:
        raise IdentitySecurityCredentialError("invalid directory credential")
    prefix, secret = parts[2], parts[3]
    row = await db.scalar(
        select(DirectoryCredential).where(
            DirectoryCredential.token_prefix == prefix,
            DirectoryCredential.is_active.is_(True),
        )
    )
    if row is None or not verify_reporter_token_secret(secret, row.token_hash):
        raise IdentitySecurityCredentialError("invalid directory credential")
    now = _utc()
    if row.expires_at is not None and _utc(row.expires_at) <= now:
        raise IdentitySecurityCredentialError("directory credential expired")
    if SCIM_USERS_WRITE_SCOPE not in set(row.scopes_json or []):
        raise IdentitySecurityCredentialError("directory credential scope is invalid")
    row.last_used_at = now
    return row


async def _affected_scope(
    db: AsyncSession, *, user_id: int
) -> tuple[set[int], dict[int, set[int]], list[ReporterCredential], list[RuntimeReportToken]]:
    membership_namespace_ids = set(
        (
            await db.scalars(
                select(NamespaceMember.namespace_id).where(NamespaceMember.user_id == user_id)
            )
        ).all()
    )
    owned_namespace_ids = set(
        (await db.scalars(select(Namespace.id).where(Namespace.owner_id == user_id))).all()
    )
    asset_rows = list(
        (
            await db.execute(
                select(AIAsset.namespace_id, AIAsset.source_runtime_id)
                .join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id)
                .where(AssetOwnership.user_id == user_id)
            )
        ).all()
    )
    credentials = list(
        (
            await db.scalars(
                select(ReporterCredential)
                .where(ReporterCredential.user_id == user_id)
                .with_for_update()
            )
        ).all()
    )
    runtime_ids = {row.runtime_id for row in credentials}
    legacy_tokens = list(
        (
            await db.scalars(
                select(RuntimeReportToken)
                .where(RuntimeReportToken.created_by == user_id)
                .with_for_update()
            )
        ).all()
    )
    runtime_ids.update(row.runtime_id for row in legacy_tokens)
    runtimes = list(
        (
            await db.scalars(
                select(RuntimeInstance).where(RuntimeInstance.id.in_(runtime_ids))
            )
        ).all()
    ) if runtime_ids else []
    runtime_namespace = {row.id: row.namespace_id for row in runtimes}
    namespaces = membership_namespace_ids | owned_namespace_ids
    runtime_ids_by_namespace: dict[int, set[int]] = {}
    for namespace_id, source_runtime_id in asset_rows:
        namespaces.add(namespace_id)
        if source_runtime_id is not None:
            runtime_ids_by_namespace.setdefault(namespace_id, set()).add(source_runtime_id)
    for runtime_id, namespace_id in runtime_namespace.items():
        namespaces.add(namespace_id)
        runtime_ids_by_namespace.setdefault(namespace_id, set()).add(runtime_id)
    return namespaces, runtime_ids_by_namespace, credentials, legacy_tokens


async def _active_user(db: AsyncSession, user_id: int | None, *, subject_id: int) -> User | None:
    if user_id is None or user_id == subject_id:
        return None
    return await db.scalar(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )


async def _handover_receivers(
    db: AsyncSession,
    *,
    namespace: Namespace,
    subject_id: int,
    profile: UserHandoverProfile,
) -> tuple[User | None, User | None]:
    preferred = await _active_user(
        db, profile.handover_receiver_user_id, subject_id=subject_id
    )
    manager = await _active_user(db, profile.manager_user_id, subject_id=subject_id)
    owner = await _active_user(db, namespace.owner_id, subject_id=subject_id)
    candidates = [row for row in (preferred, manager, owner) if row is not None]
    if not candidates:
        namespace_admin = await db.scalar(
            select(User)
            .join(NamespaceMember, NamespaceMember.user_id == User.id)
            .where(
                NamespaceMember.namespace_id == namespace.id,
                NamespaceMember.role == NamespaceRole.ADMIN,
                User.id != subject_id,
                User.is_active.is_(True),
            )
            .order_by(User.id)
        )
        if namespace_admin is not None:
            candidates.append(namespace_admin)
    if not candidates:
        system_admin = await db.scalar(
            select(User)
            .where(
                User.system_role == SystemRole.ADMIN,
                User.id != subject_id,
                User.is_active.is_(True),
            )
            .order_by(User.id)
        )
        if system_admin is not None:
            candidates.append(system_admin)
    receiver = candidates[0] if candidates else None
    fallback = next((row for row in candidates[1:] if row.id != receiver.id), None) if receiver else None
    if fallback is None:
        fallback = receiver
    return receiver, fallback


async def _ensure_offboarding_case(
    db: AsyncSession,
    *,
    namespace: Namespace,
    user: User,
    profile: UserHandoverProfile,
    runtime_ids: set[int],
    event_public_id: str,
    created_by_user_id: int,
) -> HandoverCase:
    existing = await db.scalar(
        select(HandoverCase)
        .where(
            HandoverCase.namespace_id == namespace.id,
            HandoverCase.subject_user_id == user.id,
            HandoverCase.case_type == HandoverCaseType.EMPLOYEE_OFFBOARDING,
            HandoverCase.status.notin_([HandoverStatus.CANCELLED, HandoverStatus.REJECTED]),
        )
        .order_by(HandoverCase.created_at.desc(), HandoverCase.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    receiver, fallback = await _handover_receivers(
        db,
        namespace=namespace,
        subject_id=user.id,
        profile=profile,
    )
    row = HandoverCase(
        case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
        title=f"Directory offboarding · user #{user.id}",
        subject_user_id=user.id,
        namespace_id=namespace.id,
        receiver_user_id=receiver.id if receiver else None,
        fallback_owner_user_id=fallback.id if fallback else None,
        status=HandoverStatus.DRAFT,
        risk_level=Criticality.HIGH,
        summary_json={
            "runtime_ids": sorted(runtime_ids),
            "collection_scope": {
                "users": [user.id],
                "asset_types": None,
                "include_work_traces": True,
                "include_artifacts": True,
                "lookback_days": 180,
            },
            "directory_lifecycle": {
                "event_public_id": event_public_id,
                "source": DirectoryLifecycleSource.SCIM.value,
            },
        },
        created_by=created_by_user_id,
    )
    db.add(row)
    await db.flush()
    return row


async def apply_scim_user_lifecycle(
    db: AsyncSession,
    *,
    credential: DirectoryCredential,
    external_subject: str,
    external_event_id: str,
    active: bool,
    payload: dict[str, Any],
) -> DirectoryLifecycleEvent:
    if _SAFE_DIRECTORY_EVENT_ID.fullmatch(external_event_id) is None:
        raise IdentitySecurityStateError(
            "directory event id must be an opaque safe identifier"
        )
    subject_digest = _digest({"provider_id": credential.provider_id, "subject": external_subject})
    payload_digest = _digest(payload)
    existing = await db.scalar(
        select(DirectoryLifecycleEvent).where(
            DirectoryLifecycleEvent.provider_id == credential.provider_id,
            DirectoryLifecycleEvent.external_event_id == external_event_id,
        )
    )
    if existing is not None:
        if (
            existing.subject_digest != subject_digest
            or existing.payload_digest != payload_digest
        ):
            raise IdentitySecurityConflictError(
                "directory event id was already used with different content"
            )
        return existing

    provider = await db.scalar(
        select(SSOProviderConfig)
        .where(SSOProviderConfig.id == credential.provider_id)
        .with_for_update()
    )
    if provider is None or not provider.enabled:
        raise IdentitySecurityStateError("directory provider is unavailable")
    link = await db.scalar(
        select(IdentityLink)
        .where(
            IdentityLink.provider_id == provider.id,
            IdentityLink.external_subject == external_subject,
        )
        .with_for_update()
    )
    if link is None:
        raise IdentitySecurityNotFoundError("directory subject was not found")
    user = await db.scalar(select(User).where(User.id == link.user_id).with_for_update())
    if user is None:
        raise IdentitySecurityNotFoundError("linked user was not found")

    now = _utc()
    event_public_id = directory_event_public_id()
    user_was_active = user.is_active
    profile = await db.scalar(
        select(UserHandoverProfile)
        .where(UserHandoverProfile.user_id == user.id)
        .with_for_update()
    )
    if profile is None:
        profile = UserHandoverProfile(user_id=user.id)
        db.add(profile)
        await db.flush()

    credentials_revoked = 0
    runtime_tokens_revoked = 0
    memberships_removed = 0
    role_bindings_removed = 0
    handover_cases: list[HandoverCase] = []

    if active:
        action = (
            DirectoryLifecycleAction.UPSERT
            if user.is_active and link.is_active
            else DirectoryLifecycleAction.REENABLE
        )
        user.is_active = True
        link.is_active = True
        link.disabled_at = None
        if profile.employment_status == EmploymentStatus.OFFBOARDED:
            profile.employment_status = EmploymentStatus.ACTIVE
    else:
        action = DirectoryLifecycleAction.DISABLE
        namespaces, runtime_ids_by_namespace, credentials, legacy_tokens = await _affected_scope(
            db, user_id=user.id
        )
        user.is_active = False
        link.is_active = False
        link.disabled_at = link.disabled_at or now
        profile.employment_status = EmploymentStatus.OFFBOARDED
        for reporter_credential in credentials:
            if reporter_credential.is_active and reporter_credential.revoked_at is None:
                reporter_credential.is_active = False
                reporter_credential.revoked_at = now
                reporter_credential.revoked_by = credential.created_by_user_id
                reporter_credential.revoked_reason = "directory user disabled"
                credentials_revoked += 1
        for legacy_token in legacy_tokens:
            if legacy_token.is_active:
                legacy_token.is_active = False
                runtime_tokens_revoked += 1

        namespace_rows = list(
            (
                await db.scalars(
                    select(Namespace).where(Namespace.id.in_(namespaces)).order_by(Namespace.id)
                )
            ).all()
        ) if namespaces else []
        for namespace in namespace_rows:
            handover_cases.append(
                await _ensure_offboarding_case(
                    db,
                    namespace=namespace,
                    user=user,
                    profile=profile,
                    runtime_ids=runtime_ids_by_namespace.get(namespace.id, set()),
                    event_public_id=event_public_id,
                    created_by_user_id=credential.created_by_user_id,
                )
            )
        membership_result = await db.execute(
            delete(NamespaceMember).where(NamespaceMember.user_id == user.id)
        )
        memberships_removed = int(getattr(membership_result, "rowcount", 0) or 0)
        role_result = await db.execute(delete(RoleBinding).where(RoleBinding.user_id == user.id))
        role_bindings_removed = int(getattr(role_result, "rowcount", 0) or 0)

    case_ids = [row.id for row in handover_cases]
    outcome_payload = {
        "action": action.value,
        "user_id": user.id,
        "credentials_revoked": credentials_revoked,
        "runtime_tokens_revoked": runtime_tokens_revoked,
        "memberships_removed": memberships_removed,
        "role_bindings_removed": role_bindings_removed,
        "handover_case_ids": case_ids,
    }
    event = DirectoryLifecycleEvent(
        public_id=event_public_id,
        provider_id=provider.id,
        credential_id=credential.id,
        external_event_id=external_event_id,
        subject_digest=subject_digest,
        payload_digest=payload_digest,
        action=action,
        source=DirectoryLifecycleSource.SCIM,
        user_id=user.id,
        user_was_active=user_was_active,
        credentials_revoked=credentials_revoked,
        runtime_tokens_revoked=runtime_tokens_revoked,
        memberships_removed=memberships_removed,
        role_bindings_removed=role_bindings_removed,
        handover_case_ids_json=case_ids,
        outcome_digest=_digest(outcome_payload),
        occurred_at=now,
    )
    db.add(event)
    await db.flush()
    await audit(
        db,
        username=f"scim-provider:{provider.id}",
        action=f"identity.directory_user.{action.value.lower()}",
        resource_type="directory_lifecycle_event",
        resource_id=event.id,
        details={
            "event_public_id": event.public_id,
            "provider_id": event.provider_id,
            "user_id": event.user_id,
            "credentials_revoked": event.credentials_revoked,
            "runtime_tokens_revoked": event.runtime_tokens_revoked,
            "memberships_removed": event.memberships_removed,
            "role_bindings_removed": event.role_bindings_removed,
            "handover_case_count": len(case_ids),
            "outcome_digest": event.outcome_digest,
        },
    )
    if action == DirectoryLifecycleAction.DISABLE:
        for case in handover_cases:
            if case.namespace_id is None:
                raise IdentitySecurityStateError(
                    "offboarding handover case is missing its Namespace"
                )
            await enqueue_domain_event(
                db,
                build_directory_user_disabled(
                    event,
                    namespace_id=case.namespace_id,
                    handover_case_id=case.id,
                ),
                guaranteed_new=True,
            )
    return event


def _validated_workload_scopes(
    scopes: list[str], *, principal_kind: WorkloadIdentityKind
) -> list[str]:
    normalized = sorted({scope.strip() for scope in scopes if scope.strip()})
    unknown = set(normalized) - ALLOWED_WORKLOAD_SCOPES
    if unknown:
        raise IdentitySecurityStateError(
            f"unsupported workload identity scope(s): {sorted(unknown)}"
        )
    if principal_kind == WorkloadIdentityKind.DEVICE and "release.receipt" in normalized:
        raise IdentitySecurityStateError(
            "release.receipt requires a SERVICE workload identity"
        )
    return normalized


async def _runtime_in_namespace(
    db: AsyncSession, *, runtime_id: int, namespace_id: int
) -> RuntimeInstance:
    runtime = await db.scalar(
        select(RuntimeInstance).where(
            RuntimeInstance.id == runtime_id,
            RuntimeInstance.namespace_id == namespace_id,
        )
    )
    if runtime is None:
        raise IdentitySecurityNotFoundError("Runtime was not found")
    return runtime


async def create_workload_identity(
    db: AsyncSession,
    *,
    request: WorkloadIdentityCreate,
    actor: User,
) -> tuple[ReporterCredential, str]:
    now = _utc()
    _ensure_future(request.expires_at, now=now)
    await _runtime_in_namespace(
        db, runtime_id=request.runtime_id, namespace_id=request.namespace_id
    )
    scopes = _validated_workload_scopes(
        request.scopes, principal_kind=request.principal_kind
    )
    prefix, secret, token = _token("report")
    row = ReporterCredential(
        runtime_id=request.runtime_id,
        user_id=actor.id,
        device_id=request.device_id.strip(),
        name=request.name.strip(),
        token_prefix=prefix,
        token_hash=hash_reporter_token_secret(secret),
        scopes=scopes,
        principal_kind=request.principal_kind,
        generation=1,
        expires_at=request.expires_at,
        metadata_json={"purpose": "workload_identity_v2"},
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="identity.workload_identity.created",
        resource_type="reporter_credential",
        resource_id=row.id,
        namespace_id=request.namespace_id,
        details={
            "public_id": row.public_id,
            "runtime_id": row.runtime_id,
            "principal_kind": row.principal_kind.value,
            "generation": row.generation,
            "scope_count": len(scopes),
        },
    )
    await enqueue_domain_event(
        db,
        build_workload_identity_issued(row, namespace_id=request.namespace_id),
        guaranteed_new=True,
    )
    return row, token


async def list_workload_identities(
    db: AsyncSession,
    *,
    namespace_id: int,
    runtime_id: int | None = None,
    principal_kind: WorkloadIdentityKind | None = None,
) -> list[ReporterCredential]:
    stmt = (
        select(ReporterCredential)
        .join(RuntimeInstance, RuntimeInstance.id == ReporterCredential.runtime_id)
        .where(RuntimeInstance.namespace_id == namespace_id)
        .order_by(ReporterCredential.created_at.desc(), ReporterCredential.id.desc())
    )
    if runtime_id is not None:
        stmt = stmt.where(ReporterCredential.runtime_id == runtime_id)
    if principal_kind is not None:
        stmt = stmt.where(ReporterCredential.principal_kind == principal_kind)
    return list((await db.scalars(stmt.limit(500))).all())


async def get_workload_identity(
    db: AsyncSession, *, public_id: str, lock: bool = False
) -> tuple[ReporterCredential, RuntimeInstance]:
    stmt = select(ReporterCredential).where(ReporterCredential.public_id == public_id)
    if lock:
        stmt = stmt.with_for_update()
    row = await db.scalar(stmt)
    if row is None:
        raise IdentitySecurityNotFoundError("workload identity was not found")
    runtime = await db.scalar(
        select(RuntimeInstance).where(RuntimeInstance.id == row.runtime_id)
    )
    if runtime is None:
        raise IdentitySecurityNotFoundError("workload identity Runtime was not found")
    return row, runtime


async def rotate_workload_identity(
    db: AsyncSession,
    *,
    public_id: str,
    reason: str,
    expires_at: datetime | None,
    actor: User,
) -> tuple[ReporterCredential, str, int]:
    now = _utc()
    _ensure_future(expires_at, now=now)
    old, runtime = await get_workload_identity(db, public_id=public_id, lock=True)
    if not old.is_active or old.revoked_at is not None:
        raise IdentitySecurityConflictError("workload identity is not active")
    old.is_active = False
    old.revoked_at = now
    old.revoked_by = actor.id
    old.revoked_reason = "rotated"
    prefix, secret, token = _token("report")
    successor = ReporterCredential(
        runtime_id=old.runtime_id,
        user_id=old.user_id,
        device_id=old.device_id,
        name=old.name,
        token_prefix=prefix,
        token_hash=hash_reporter_token_secret(secret),
        scopes=list(old.scopes or []),
        principal_kind=old.principal_kind,
        generation=old.generation + 1,
        expires_at=expires_at if expires_at is not None else old.expires_at,
        rotated_from_id=old.id,
        metadata_json={
            **(old.metadata_json or {}),
            "rotation_source": "workload_identity_v2",
        },
    )
    db.add(successor)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="identity.workload_identity.rotated",
        resource_type="reporter_credential",
        resource_id=successor.id,
        namespace_id=runtime.namespace_id,
        details={
            "previous_public_id": old.public_id,
            "successor_public_id": successor.public_id,
            "runtime_id": successor.runtime_id,
            "principal_kind": successor.principal_kind.value,
            "generation": successor.generation,
            "reason_length": len(reason),
        },
    )
    await enqueue_domain_event(
        db,
        build_workload_identity_rotated(
            old, successor, namespace_id=runtime.namespace_id
        ),
        guaranteed_new=True,
    )
    return successor, token, runtime.namespace_id


async def revoke_workload_identity(
    db: AsyncSession,
    *,
    public_id: str,
    reason: str,
    actor: User,
) -> tuple[ReporterCredential, int]:
    row, runtime = await get_workload_identity(db, public_id=public_id, lock=True)
    if not row.is_active or row.revoked_at is not None:
        raise IdentitySecurityConflictError("workload identity is already revoked")
    row.is_active = False
    row.revoked_at = _utc()
    row.revoked_by = actor.id
    row.revoked_reason = reason
    await db.flush()
    await audit(
        db,
        user=actor,
        action="identity.workload_identity.revoked",
        resource_type="reporter_credential",
        resource_id=row.id,
        namespace_id=runtime.namespace_id,
        details={
            "public_id": row.public_id,
            "runtime_id": row.runtime_id,
            "principal_kind": row.principal_kind.value,
            "generation": row.generation,
            "reason_length": len(reason),
        },
    )
    await enqueue_domain_event(
        db,
        build_workload_identity_revoked(row, namespace_id=runtime.namespace_id),
        guaranteed_new=True,
    )
    return row, runtime.namespace_id
