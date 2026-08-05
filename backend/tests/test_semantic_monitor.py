from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.evaluation import (
    EvaluationSemanticClusteringRun,
    EvaluationSemanticMonitorAlert,
    EvaluationSemanticMonitorAlertStatus,
    EvaluationSemanticMonitorRunStatus,
)
from app.models.outbox import OutboxEvent
from app.services.evaluation_semantic_monitor_service import (
    execute_evaluation_semantic_monitor_run,
    lease_evaluation_semantic_monitor_run,
)
from test_evaluation_case_routing import _client
from test_failure_taxonomy_experience import _route
from test_semantic_clustering_regression import _VectorAdapter, _semantic_run


@pytest.mark.asyncio
async def test_semantic_monitor_repeats_exact_source_and_opens_acknowledgeable_alert(
    async_session,
) -> None:
    user, namespace, routing_version, routing_run = await _route(
        async_session, "semantic-monitor", recurring=False
    )
    baseline = await _semantic_run(
        async_session,
        user=user,
        namespace=namespace,
        routing_version=routing_version,
        routing_run=routing_run,
        suffix="monitor-baseline",
        vectors=((1.0, 0.0), (0.99, 0.01)),
    )

    async with _client(async_session, user) as client:
        policy_response = await client.post(
            "/api/v2/evaluation-semantic-regression-policies",
            json={
                "namespace_id": namespace.id,
                "name": "monitor-regression-policy",
            },
        )
        assert policy_response.status_code == 201
        version_response = await client.post(
            f"/api/v2/evaluation-semantic-regression-policies/{policy_response.json()['public_id']}/versions",
            json={
                "minimum_pairwise_assignment_agreement": 0.9,
                "maximum_cluster_count_change_ratio": 0.25,
                "maximum_mean_centroid_similarity_drop": 0.1,
                "maximum_eligible_cluster_ratio_drop": 0.25,
            },
        )
        assert version_response.status_code == 201

        monitor_response = await client.post(
            "/api/v2/evaluation-semantic-monitors",
            json={
                "namespace_id": namespace.id,
                "name": "bad-case-drift-watch",
                "baseline_run_public_id": baseline.public_id,
                "candidate_policy_version_public_id": baseline.policy_version.public_id,
                "regression_policy_version_public_id": version_response.json()["public_id"],
                "interval_seconds": 300,
            },
        )
        assert monitor_response.status_code == 201
        monitor = monitor_response.json()
        assert monitor["status"] == "ACTIVE"
        assert monitor["baseline_run_public_id"] == baseline.public_id

        paused_response = await client.post(
            f"/api/v2/evaluation-semantic-monitors/{monitor['public_id']}/pause"
        )
        assert paused_response.status_code == 200
        assert paused_response.json()["status"] == "PAUSED"
        resumed_response = await client.post(
            f"/api/v2/evaluation-semantic-monitors/{monitor['public_id']}/resume"
        )
        assert resumed_response.status_code == 200
        assert resumed_response.json()["status"] == "ACTIVE"

        stable_queue_response = await client.post(
            f"/api/v2/evaluation-semantic-monitors/{monitor['public_id']}/run-now",
            headers={"Idempotency-Key": "semantic-monitor-stable-now"},
        )
        assert stable_queue_response.status_code == 202
        stable_run_public_id = stable_queue_response.json()["public_id"]

    stable_worker = "test-worker:stable"
    stable_lease = await lease_evaluation_semantic_monitor_run(
        async_session,
        run_public_id=stable_run_public_id,
        worker_id=stable_worker,
        lease_seconds=300,
        max_attempts=3,
    )
    assert stable_lease is not None
    stable_run = await execute_evaluation_semantic_monitor_run(
        async_session,
        run_public_id=stable_run_public_id,
        worker_id=stable_worker,
        adapter=_VectorAdapter(((1.0, 0.0), (0.99, 0.01))),
    )
    assert stable_run is not None
    assert stable_run.status == EvaluationSemanticMonitorRunStatus.COMPLETED
    assert stable_run.outcome.value == "PASS"
    assert stable_run.alert is None
    await async_session.commit()

    # The baseline and stable candidate intentionally share exact evidence.
    repeated_count = await async_session.scalar(
        select(func.count(EvaluationSemanticClusteringRun.id)).where(
            EvaluationSemanticClusteringRun.policy_version_id
            == baseline.policy_version_id,
            EvaluationSemanticClusteringRun.source_case_routing_run_id
            == baseline.source_case_routing_run_id,
            EvaluationSemanticClusteringRun.evidence_digest
            == baseline.evidence_digest,
        )
    )
    assert repeated_count == 2

    async with _client(async_session, user) as client:
        drift_queue_response = await client.post(
            f"/api/v2/evaluation-semantic-monitors/{monitor['public_id']}/run-now",
            headers={"Idempotency-Key": "semantic-monitor-drift-now"},
        )
        assert drift_queue_response.status_code == 202
        drift_run_public_id = drift_queue_response.json()["public_id"]

    drift_worker = "test-worker:drift"
    drift_lease = await lease_evaluation_semantic_monitor_run(
        async_session,
        run_public_id=drift_run_public_id,
        worker_id=drift_worker,
        lease_seconds=300,
        max_attempts=3,
    )
    assert drift_lease is not None
    drift_run = await execute_evaluation_semantic_monitor_run(
        async_session,
        run_public_id=drift_run_public_id,
        worker_id=drift_worker,
        adapter=_VectorAdapter(((1.0, 0.0), (0.0, 1.0))),
    )
    assert drift_run is not None
    assert drift_run.status == EvaluationSemanticMonitorRunStatus.COMPLETED
    assert drift_run.outcome.value == "DRIFTED"
    assert drift_run.alert is not None
    assert drift_run.alert.severity.value == "CRITICAL"
    alert_public_id = drift_run.alert.public_id
    await async_session.commit()

    async with _client(async_session, user) as client:
        alerts_response = await client.get(
            "/api/v2/evaluation-semantic-monitor-alerts",
            params={"namespace_id": namespace.id, "status": "OPEN"},
        )
        assert alerts_response.status_code == 200
        assert [value["public_id"] for value in alerts_response.json()] == [
            alert_public_id
        ]
        acknowledge_response = await client.post(
            f"/api/v2/evaluation-semantic-monitor-alerts/{alert_public_id}/acknowledge",
            json={"note": "Investigated locally; keep the pinned baseline."},
        )
        assert acknowledge_response.status_code == 200
        assert acknowledge_response.json()["status"] == "ACKNOWLEDGED"
        replay_response = await client.post(
            f"/api/v2/evaluation-semantic-monitor-alerts/{alert_public_id}/acknowledge",
            json={"note": "must not overwrite"},
        )
        assert replay_response.status_code == 409

    alert = await async_session.scalar(
        select(EvaluationSemanticMonitorAlert).where(
            EvaluationSemanticMonitorAlert.public_id == alert_public_id
        )
    )
    assert alert is not None
    assert alert.status == EvaluationSemanticMonitorAlertStatus.ACKNOWLEDGED

    events = list(
        (
            await async_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type.in_(
                        (
                            "EvaluationSemanticMonitorCreated",
                            "EvaluationSemanticMonitorRunQueued",
                            "EvaluationSemanticMonitorRunCompleted",
                            "EvaluationSemanticMonitorAlertOpened",
                            "EvaluationSemanticMonitorAlertAcknowledged",
                        )
                    )
                )
            )
        ).all()
    )
    assert len(events) == 7
    serialized_events = json.dumps([event.payload_json for event in events])
    assert "Investigated locally" not in serialized_events
    assert "same-private-content" not in serialized_events
    assert '"vector"' not in serialized_events

    audits = list(
        (
            await async_session.scalars(
                select(AuditLog).where(
                    AuditLog.action.like("evaluation_semantic_monitor%")
                )
            )
        ).all()
    )
    serialized_audits = json.dumps([value.details for value in audits])
    assert "Investigated locally" not in serialized_audits
    assert "same-private-content" not in serialized_audits
