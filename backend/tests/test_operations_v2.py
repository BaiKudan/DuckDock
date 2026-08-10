from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.metrics import http_metrics
from app.models.operations import (
    OpsIncidentStatus,
    OpsRecoveryDrillStatus,
    OpsSLOEvaluationStatus,
)
from app.models.user import SystemRole, User
from app.schemas.operations import OpsRecoveryDrillCreate
from app.services.operations_service import (
    OperationsConflictError,
    acknowledge_ops_incident,
    evaluate_operations_slo,
    list_ops_incidents,
    record_recovery_drill,
    resolve_ops_incident,
)


def test_operations_openapi_and_prometheus_surface_are_explicit() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    for path in (
        "/api/v2/operations/overview",
        "/api/v2/operations/slo-evaluations",
        "/api/v2/operations/incidents",
        "/api/v2/operations/incidents/{public_id}/acknowledge",
        "/api/v2/operations/incidents/{public_id}/resolve",
        "/api/v2/operations/recovery-drills",
    ):
        assert path in paths

    http_metrics.reset()
    http_metrics.observe(
        method="GET",
        route="/api/v2/agent-runs",
        status_code=200,
        duration=0.015,
    )
    rendered = http_metrics.render_prometheus()
    assert "duckdock_http_requests_total" in rendered
    assert 'route="/api/v2/agent-runs"' in rendered
    assert "duckdock_process_up 1" in rendered


async def _admin(db) -> User:
    suffix = uuid4().hex[:8]
    row = User(
        username=f"ops-admin-{suffix}",
        email=f"ops-admin-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.ADMIN,
        is_active=True,
    )
    db.add(row)
    await db.flush()
    return row


def _seed_http_metrics(*, policy_duration: float = 0.05, failure_count: int = 0) -> None:
    http_metrics.reset()
    for index in range(25):
        status = 500 if index < failure_count else 200
        http_metrics.observe(
            method="POST",
            route="/api/v2/reporter/runs",
            status_code=status,
            duration=0.05,
        )
        http_metrics.observe(
            method="GET",
            route="/api/v2/agent-runs",
            status_code=200,
            duration=0.2,
        )
        http_metrics.observe(
            method="POST",
            route="/api/v2/release-candidates/{public_id}/policy-evaluations",
            status_code=200,
            duration=policy_duration,
        )


@pytest.mark.asyncio
async def test_slo_evaluation_is_immutable_and_incident_lifecycle_is_explicit(async_session):
    actor = await _admin(async_session)
    _seed_http_metrics()
    healthy = await evaluate_operations_slo(
        async_session,
        idempotency_key="ops-slo-healthy-001",
        window_minutes=15,
        actor=actor,
    )
    assert healthy.status == OpsSLOEvaluationStatus.HEALTHY
    assert healthy.request_count == 75
    assert healthy.evidence_ingest_p95_ms == 50.0

    replay = await evaluate_operations_slo(
        async_session,
        idempotency_key="ops-slo-healthy-001",
        window_minutes=15,
        actor=actor,
    )
    assert replay.id == healthy.id
    with pytest.raises(OperationsConflictError):
        await evaluate_operations_slo(
            async_session,
            idempotency_key="ops-slo-healthy-001",
            window_minutes=30,
            actor=actor,
        )

    _seed_http_metrics(policy_duration=0.35, failure_count=2)
    breached = await evaluate_operations_slo(
        async_session,
        idempotency_key="ops-slo-breached-001",
        window_minutes=15,
        actor=actor,
    )
    assert breached.status == OpsSLOEvaluationStatus.BREACHED
    assert "HTTP_ERROR_RATIO_EXCEEDED" in breached.reason_codes_json
    assert "POLICY_DECISION_P95_EXCEEDED" in breached.reason_codes_json
    incidents = await list_ops_incidents(async_session, limit=10)
    assert len(incidents) == 1
    assert incidents[0].status == OpsIncidentStatus.OPEN

    acknowledged = await acknowledge_ops_incident(
        async_session,
        public_id=incidents[0].public_id,
        actor=actor,
        note="owner investigating",
    )
    assert acknowledged.status == OpsIncidentStatus.ACKNOWLEDGED
    resolved = await resolve_ops_incident(
        async_session,
        public_id=acknowledged.public_id,
        actor=actor,
        note="latency returned below threshold",
    )
    assert resolved.status == OpsIncidentStatus.RESOLVED


@pytest.mark.asyncio
async def test_recovery_drill_derives_status_and_detects_conflicting_replay(async_session):
    actor = await _admin(async_session)
    started = datetime.now(timezone.utc) - timedelta(seconds=8)
    request = OpsRecoveryDrillCreate(
        idempotency_key="ops-recovery-dev-001",
        environment="local-dev",
        git_head="a" * 40,
        backup_set_digest="b" * 64,
        mysql_digest="c" * 64,
        object_store_digest="d" * 64,
        mysql_row_count=3,
        object_count=1,
        rpo_seconds=0,
        rto_seconds=8,
        started_at=started,
        finished_at=started + timedelta(seconds=8),
    )
    row = await record_recovery_drill(async_session, request=request, actor=actor)
    assert row.status == OpsRecoveryDrillStatus.PASSED
    assert row.reason_codes_json == []
    replay = await record_recovery_drill(async_session, request=request, actor=actor)
    assert replay.id == row.id

    with pytest.raises(OperationsConflictError):
        await record_recovery_drill(
            async_session,
            request=request.model_copy(update={"mysql_row_count": 4}),
            actor=actor,
        )

    failed = await record_recovery_drill(
        async_session,
        request=request.model_copy(
            update={
                "idempotency_key": "ops-recovery-dev-002",
                "rpo_seconds": 901,
                "object_count": 0,
            }
        ),
        actor=actor,
    )
    assert failed.status == OpsRecoveryDrillStatus.FAILED
    assert failed.reason_codes_json == ["OBJECT_STORE_RESTORE_EMPTY", "RPO_EXCEEDED"]
