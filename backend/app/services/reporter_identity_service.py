"""Credential-derived identity and trust for the v2 execution ingress.

Reporter request bodies are deliberately not part of this module. The only
governance identity accepted here is the authenticated ReporterCredential and
its Runtime -> Namespace relationship.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_token_security import verify_reporter_token_secret
from app.models.control_plane import (
    ReporterCredential,
    RuntimeInstance,
    RuntimeStatus,
)
from app.models.execution import TrustLevel, TrustSource
from app.models.namespace import Namespace
from app.services.report_upload_service import (
    ReporterAuthContext,
    authenticate_runtime_report_token,
)
from app.services.tenant_write_service import (
    InactiveNamespaceError,
    MissingNamespaceError,
    require_active_namespace,
)
from app.services.telemetry_ports import AttestationVerifierPort


EXECUTION_WRITE_SCOPE = "execution.write"
RELEASE_RECEIPT_SCOPE = "release.receipt"


class ReporterIdentityError(ValueError):
    """Base class for Reporter execution identity failures."""


class ReporterCredentialRequiredError(ReporterIdentityError):
    """The v2 execution API does not accept legacy RuntimeReportToken tokens."""


class ReporterScopeError(ReporterIdentityError):
    """The credential is valid but lacks the execution write scope."""


class ReporterTenantError(ReporterIdentityError):
    """The credential no longer resolves to one active tenant/runtime pair."""


@dataclass(frozen=True, slots=True)
class ReporterTrustDecision:
    trust_level: TrustLevel
    trust_source: TrustSource = TrustSource.REPORTER
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class ReporterExecutionIdentity:
    namespace_id: int
    runtime_id: int
    credential_id: int
    actor_user_id: int | None
    trust_level: TrustLevel
    trust_source: TrustSource


async def derive_reporter_trust(
    *,
    attestation_proof: dict[str, Any] | None = None,
    verifier: AttestationVerifierPort | None = None,
) -> ReporterTrustDecision:
    """Return bearer trust unless an explicit verifier accepts a proof."""

    if attestation_proof is None:
        return ReporterTrustDecision(
            trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        )
    verified = False
    if verifier is not None:
        try:
            verified = await verifier.verify(attestation_proof)
        except Exception:
            verified = False
    if not verified:
        return ReporterTrustDecision(
            trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
            degraded=True,
        )
    return ReporterTrustDecision(
        trust_level=TrustLevel.PRODUCER_ATTESTED,
    )


async def resolve_reporter_execution_identity(
    db: AsyncSession,
    reporter: ReporterAuthContext,
    *,
    required_scope: str = EXECUTION_WRITE_SCOPE,
) -> ReporterExecutionIdentity:
    """Resolve ReporterCredential -> Runtime -> active Namespace server-side."""

    if (
        reporter.source != "reporter_credential"
        or not isinstance(reporter.token, ReporterCredential)
    ):
        raise ReporterCredentialRequiredError(
            "ReporterCredential is required for execution ingestion"
        )
    scopes = {
        scope
        for scope in (reporter.token.scopes or [])
        if isinstance(scope, str)
    }
    if required_scope not in scopes:
        raise ReporterScopeError(
            f"Reporter credential is missing {required_scope} scope"
        )

    runtime = (
        await db.execute(
            select(RuntimeInstance).where(
                RuntimeInstance.id == reporter.token.runtime_id,
            )
        )
    ).scalar_one_or_none()
    if runtime is None or runtime.id != reporter.runtime.id:
        raise ReporterTenantError(
            "Reporter credential does not resolve to one Runtime"
        )
    if runtime.status == RuntimeStatus.DISABLED:
        raise ReporterTenantError("Reporter Runtime is disabled")
    try:
        namespace = await require_active_namespace(db, runtime.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise ReporterTenantError(
            "Reporter Runtime does not resolve to an active Namespace"
        ) from exc

    trust = await derive_reporter_trust()
    return ReporterExecutionIdentity(
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        credential_id=reporter.token.id,
        actor_user_id=reporter.token.user_id,
        trust_level=trust.trust_level,
        trust_source=trust.trust_source,
    )


async def authenticate_reporter_execution_credential(
    db: AsyncSession,
    token: str,
    *,
    required_scope: str = EXECUTION_WRITE_SCOPE,
) -> ReporterExecutionIdentity:
    """Authenticate and fully resolve the v2 Reporter execution identity."""

    parts = token.split("_", 3)
    if (
        len(parts) == 4
        and parts[0] == "dkr"
        and parts[1] == "report"
    ):
        prefix, secret = parts[2], parts[3]
        row = (
            await db.execute(
                select(
                    ReporterCredential,
                    RuntimeInstance,
                    Namespace,
                )
                .join(
                    RuntimeInstance,
                    RuntimeInstance.id
                    == ReporterCredential.runtime_id,
                )
                .join(
                    Namespace,
                    Namespace.id
                    == RuntimeInstance.namespace_id,
                )
                .where(
                    ReporterCredential.token_prefix == prefix,
                    ReporterCredential.is_active.is_(True),
                    ReporterCredential.revoked_at.is_(None),
                    Namespace.deleted_at.is_(None),
                )
            )
        ).first()
        if row is not None:
            credential, runtime, namespace = row
            if not verify_reporter_token_secret(
                secret,
                credential.token_hash,
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid reporter credential",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            now = datetime.now(timezone.utc)
            expires_at = credential.expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(
                        tzinfo=timezone.utc
                    )
                if expires_at < now:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Reporter credential expired",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
            scopes = {
                scope
                for scope in (credential.scopes or [])
                if isinstance(scope, str)
            }
            if required_scope not in scopes:
                raise ReporterScopeError(
                    f"Reporter credential is missing {required_scope} scope"
                )
            if runtime.status == RuntimeStatus.DISABLED:
                raise ReporterTenantError(
                    "Reporter Runtime is disabled"
                )
            trust = await derive_reporter_trust()
            return ReporterExecutionIdentity(
                namespace_id=namespace.id,
                runtime_id=runtime.id,
                credential_id=credential.id,
                actor_user_id=credential.user_id,
                trust_level=trust.trust_level,
                trust_source=trust.trust_source,
            )

    reporter = await authenticate_runtime_report_token(
        db,
        token,
        touch_usage=False,
    )
    return await resolve_reporter_execution_identity(
        db,
        reporter,
        required_scope=required_scope,
    )


async def authenticate_reporter_release_credential(
    db: AsyncSession,
    token: str,
) -> ReporterExecutionIdentity:
    """Authenticate a Runtime credential authorized to return release receipts."""

    return await authenticate_reporter_execution_credential(
        db,
        token,
        required_scope=RELEASE_RECEIPT_SCOPE,
    )
