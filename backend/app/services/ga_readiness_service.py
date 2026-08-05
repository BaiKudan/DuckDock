from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.openapi_v2 import (
    CONTRACT_VERSION,
    EXPECTED_CONTRACT_SHA256,
)
from app.core.config import settings
from app.core.time import ensure_utc
from app.models.compatibility import ReconciliationStatus, V1V2Reconciliation
from app.models.control_plane import RuntimeInstance, RuntimeStatus
from app.models.handover import HandoverSignedPackage
from app.models.iam import DirectoryLifecycleAction, DirectoryLifecycleEvent
from app.models.operations import (
    OpsIncident,
    OpsIncidentStatus,
    OpsRecoveryDrill,
    OpsRecoveryDrillStatus,
    OpsSLOEvaluation,
    OpsSLOEvaluationStatus,
)
from app.models.package_registry import (
    AgentPackageVersion,
    AgentPackageVersionStatus,
    PackageSigningKey,
)
from app.models.release_control import (
    ReleaseDeploymentReceipt,
    ReleaseReceiptStatus,
)
from app.services.operations_service import THRESHOLDS
from app.services.outbox_dispatcher_service import get_outbox_health


PROFILE_VERSION = "duckdock-2-ga-readiness-v1"
EXPECTED_DB_REVISION = "20260804_0062"
SLO_FRESHNESS = timedelta(hours=1)
RECOVERY_FRESHNESS = timedelta(days=7)

PrometheusProbe = Callable[[], Awaitable[tuple[bool, str]]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _count(db: AsyncSession, model: Any, *conditions: Any) -> int:
    stmt = select(func.count(model.id))
    if conditions:
        stmt = stmt.where(*conditions)
    return int(await db.scalar(stmt) or 0)


async def _database_revision(db: AsyncSession) -> str | None:
    connection = await db.connection()
    exists = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).has_table("alembic_version")
    )
    if not exists:
        return None
    return await db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))


async def _probe_prometheus() -> tuple[bool, str]:
    url = f"{settings.PROMETHEUS_BASE_URL.rstrip('/')}/-/ready"
    try:
        async with httpx.AsyncClient(
            timeout=settings.PROMETHEUS_READINESS_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(url)
        return response.status_code == 200, f"HTTP {response.status_code} · {url}"
    except Exception as exc:
        return False, f"{type(exc).__name__} · {url}"


async def get_ga_readiness(
    db: AsyncSession,
    *,
    openapi_contract_digest: str,
    prometheus_probe: PrometheusProbe | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate technical release-readiness gates without mutating evidence."""

    current = ensure_utc(now or _utcnow())
    checks: list[dict[str, str]] = []

    def add(
        key: str,
        title: str,
        status: str,
        observed: Any,
        expected: str,
        detail: str,
    ) -> None:
        checks.append(
            {
                "key": key,
                "title": title,
                "status": status,
                "observed": str(observed),
                "expected": expected,
                "detail": detail,
            }
        )

    db_revision = await _database_revision(db)
    add(
        "database_revision",
        "Database migration head",
        "PASS" if db_revision == EXPECTED_DB_REVISION else "BLOCK",
        db_revision or "missing",
        EXPECTED_DB_REVISION,
        "The live database must match the reviewed release schema head.",
    )

    contract_matches = (
        EXPECTED_CONTRACT_SHA256 != "PENDING"
        and openapi_contract_digest == EXPECTED_CONTRACT_SHA256
    )
    add(
        "openapi_v2_contract",
        "Frozen API v2 contract",
        "PASS" if contract_matches else "BLOCK",
        openapi_contract_digest,
        EXPECTED_CONTRACT_SHA256,
        "The live /api/v2 schema must byte-match the committed release snapshot.",
    )

    runtime_count = await _count(
        db,
        RuntimeInstance,
        RuntimeInstance.status.in_([RuntimeStatus.ACTIVE, RuntimeStatus.DEGRADED]),
    )
    add(
        "runtime_inventory",
        "Runtime inventory",
        "PASS" if runtime_count > 0 else "BLOCK",
        runtime_count,
        ">= 1",
        "At least one manageable Runtime must be present.",
    )

    package_count = await _count(
        db,
        AgentPackageVersion,
        AgentPackageVersion.status == AgentPackageVersionStatus.VERIFIED,
    )
    add(
        "verified_package",
        "Verified Agent Package",
        "PASS" if package_count > 0 else "BLOCK",
        package_count,
        ">= 1",
        "Release subjects must be immutable, signed PackageVersions.",
    )

    receipt_count = await _count(
        db,
        ReleaseDeploymentReceipt,
        ReleaseDeploymentReceipt.status == ReleaseReceiptStatus.APPLIED,
    )
    add(
        "release_receipt",
        "Applied release receipt",
        "PASS" if receipt_count > 0 else "BLOCK",
        receipt_count,
        ">= 1",
        "A Runtime-authenticated applied promotion or rollback receipt is required.",
    )

    signed_handover_count = await _count(db, HandoverSignedPackage)
    add(
        "signed_handover",
        "Signed Handover 2.0 package",
        "PASS" if signed_handover_count > 0 else "BLOCK",
        signed_handover_count,
        ">= 1",
        "The handover graph, obligations and acceptance must reach a signed package.",
    )

    disable_count = await _count(
        db,
        DirectoryLifecycleEvent,
        DirectoryLifecycleEvent.action == DirectoryLifecycleAction.DISABLE,
    )
    add(
        "directory_offboarding",
        "Directory offboarding evidence",
        "PASS" if disable_count > 0 else "BLOCK",
        disable_count,
        ">= 1",
        "SCIM disable must revoke access and create durable lifecycle evidence.",
    )

    rotation_sequence = int(
        await db.scalar(select(func.max(PackageSigningKey.rotation_sequence))) or 0
    )
    add(
        "signing_key_rotation",
        "Signing-key rotation",
        "PASS" if rotation_sequence >= 2 else "BLOCK",
        rotation_sequence,
        ">= 2",
        "A verified key rotation proves that upgrade and revocation paths work.",
    )

    mismatch_count = await _count(
        db,
        V1V2Reconciliation,
        V1V2Reconciliation.status == ReconciliationStatus.MISMATCH,
    )
    add(
        "v1_v2_reconciliation",
        "v1/v2 reconciliation",
        "PASS" if mismatch_count == 0 else "BLOCK",
        mismatch_count,
        "0 unexplained differences",
        "Expected legacy-only records are allowed; tenant or linkage mismatches are not.",
    )

    outbox = await get_outbox_health(db, now=current)
    outbox_ok = (
        outbox.failed_count == 0
        and outbox.expired_lease_count == 0
        and (
            outbox.oldest_pending_age_seconds is None
            or outbox.oldest_pending_age_seconds
            <= int(THRESHOLDS["outbox_pending_age_seconds_max"])
        )
    )
    add(
        "transactional_outbox",
        "Transactional outbox",
        "PASS" if outbox_ok else "BLOCK",
        (
            f"failed={outbox.failed_count}, expired={outbox.expired_lease_count}, "
            f"oldest={outbox.oldest_pending_age_seconds or 0}s"
        ),
        "failed=0, expired=0, oldest<=300s",
        "Committed governance events must remain dispatchable and replay-safe.",
    )

    latest_drill = await db.scalar(
        select(OpsRecoveryDrill).order_by(OpsRecoveryDrill.finished_at.desc()).limit(1)
    )
    if latest_drill is None:
        recovery_status = "BLOCK"
        recovery_observed = "missing"
    else:
        recovery_age = current - ensure_utc(latest_drill.finished_at)
        recovery_passed = (
            latest_drill.status == OpsRecoveryDrillStatus.PASSED
            and latest_drill.rpo_seconds <= int(THRESHOLDS["recovery_rpo_seconds_max"])
            and latest_drill.rto_seconds <= int(THRESHOLDS["recovery_rto_seconds_max"])
        )
        recovery_status = (
            "BLOCK" if not recovery_passed else "WARN" if recovery_age > RECOVERY_FRESHNESS else "PASS"
        )
        recovery_observed = (
            f"{latest_drill.status.value}, RPO={latest_drill.rpo_seconds}s, "
            f"RTO={latest_drill.rto_seconds}s, age={int(recovery_age.total_seconds())}s"
        )
    add(
        "recovery_drill",
        "Recovery drill",
        recovery_status,
        recovery_observed,
        "PASSED within 7d; RPO<=900s; RTO<=14400s",
        "The receipt must come from a real MySQL and object-store restore drill.",
    )

    latest_slo = await db.scalar(
        select(OpsSLOEvaluation).order_by(OpsSLOEvaluation.evaluated_at.desc()).limit(1)
    )
    if latest_slo is None:
        slo_status = "BLOCK"
        slo_observed = "missing"
    else:
        slo_age = current - ensure_utc(latest_slo.evaluated_at)
        if latest_slo.status == OpsSLOEvaluationStatus.BREACHED:
            slo_status = "BLOCK"
        elif latest_slo.status == OpsSLOEvaluationStatus.DEGRADED or slo_age > SLO_FRESHNESS:
            slo_status = "WARN"
        else:
            slo_status = "PASS"
        slo_observed = (
            f"{latest_slo.status.value}, samples={latest_slo.request_count}, "
            f"age={int(slo_age.total_seconds())}s"
        )
    add(
        "ga_slo",
        "Release SLO evaluation",
        slo_status,
        slo_observed,
        "HEALTHY within 1h",
        "DEGRADED is a visible gap; BREACHED blocks the candidate.",
    )

    active_incident_count = await _count(
        db,
        OpsIncident,
        OpsIncident.status != OpsIncidentStatus.RESOLVED,
    )
    add(
        "operations_incidents",
        "Open operations incidents",
        "PASS" if active_incident_count == 0 else "BLOCK",
        active_incident_count,
        "0",
        "Every SLO breach must be resolved before release approval.",
    )

    prometheus_ok, prometheus_observed = await (prometheus_probe or _probe_prometheus)()
    add(
        "prometheus",
        "Prometheus readiness",
        "PASS" if prometheus_ok else "BLOCK",
        prometheus_observed,
        "HTTP 200 from /-/ready",
        "Metrics and alert rules are mandatory for an approved production release.",
    )

    pass_count = sum(item["status"] == "PASS" for item in checks)
    warn_count = sum(item["status"] == "WARN" for item in checks)
    block_count = sum(item["status"] == "BLOCK" for item in checks)
    status = "BLOCKED" if block_count else "READY_WITH_GAPS" if warn_count else "READY"
    return {
        "profile_version": PROFILE_VERSION,
        "contract_version": CONTRACT_VERSION,
        "contract_digest": openapi_contract_digest,
        "expected_db_revision": EXPECTED_DB_REVISION,
        "current_db_revision": db_revision,
        "status": status,
        "pass_count": pass_count,
        "warn_count": warn_count,
        "block_count": block_count,
        "checked_at": current,
        "checks": checks,
    }
