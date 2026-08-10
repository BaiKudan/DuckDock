"""FND-031/035/036/040-043 Reporter and management API v2 tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v2.router import api_router
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.audit import AuditLog
from app.models.control_plane import (
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeReportToken,
    RuntimeStatus,
)
from app.models.execution import AgentSession, AgentSessionStatus, TrustLevel, TrustSource
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import SystemRole, User
from app.schemas.execution import RunStartEnvelope
from app.services.execution_service import start_run
from app.services.reporter_identity_service import (
    EXECUTION_WRITE_SCOPE,
    derive_reporter_trust,
)


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _enable_execution_ingestion(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AGENT_EXECUTION_INGESTION_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "AGENT_EXECUTION_RUNTIME_ALLOWLIST",
        [],
    )


async def _user(db, suffix: str) -> User:
    user = User(
        username=f"execution-api-{suffix}",
        email=f"execution-api-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add(user)
    await db.flush()
    return user


async def _tenant(db, owner: User, suffix: str) -> tuple[Namespace, RuntimeInstance]:
    namespace = Namespace(
        name=f"execution-api-{suffix}",
        owner_id=owner.id,
    )
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=owner.id,
            role=NamespaceRole.ADMIN,
        )
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"execution-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(runtime)
    await db.flush()
    return namespace, runtime


async def _reporter_credential(
    db,
    *,
    runtime: RuntimeInstance,
    user: User,
    suffix: str,
    scopes: list[str] | None = None,
) -> tuple[ReporterCredential, str]:
    prefix = f"e{suffix}"[:16]
    secret = f"reporter-secret-{suffix}"
    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=user.id,
        device_id=f"device-{suffix}",
        name=f"Execution Reporter {suffix}",
        token_prefix=prefix,
        token_hash=hash_password(secret),
        scopes=scopes
        if scopes is not None
        else [EXECUTION_WRITE_SCOPE],
    )
    db.add(credential)
    await db.flush()
    return credential, f"dkr_report_{prefix}_{secret}"


@asynccontextmanager
async def _client(db):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v2")

    async def override_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


def _session_body(external_session_id: str = "session-api-1") -> dict:
    return {
        "external_session_id": external_session_id,
        "started_at": NOW.isoformat(),
        "sensitivity": "RESTRICTED",
        "content_capture_mode": "metadata_only",
        "metadata": {"service.name": "agent-loop"},
    }


def _run_body(
    external_run_id: str = "run-api-1",
    *,
    session_public_id: str | None = None,
    started_at: datetime = NOW,
) -> dict:
    body = {
        "external_run_id": external_run_id,
        "started_at": started_at.isoformat(),
        "source_schema": "duckdock-run-envelope",
        "source_schema_version": "1.0",
        "content_capture_mode": "metadata_only",
        "metadata": {"operation.name": "agent.run"},
    }
    if session_public_id is not None:
        body["session_public_id"] = session_public_id
    return body


def _reporter_headers(token: str, idempotency_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }


async def test_execution_ingestion_flag_and_runtime_allowlist_support_backout(
    async_session,
    monkeypatch,
) -> None:
    owner = await _user(async_session, "rollout-owner")
    _, runtime = await _tenant(async_session, owner, "rollout-tenant")
    _, token = await _reporter_credential(
        async_session,
        runtime=runtime,
        user=owner,
        suffix="rollout",
    )
    other_owner = await _user(async_session, "rollout-other-owner")
    _, other_runtime = await _tenant(
        async_session,
        other_owner,
        "rollout-other-tenant",
    )
    _, other_token = await _reporter_credential(
        async_session,
        runtime=other_runtime,
        user=other_owner,
        suffix="rolloutother",
    )
    runtime_id = runtime.id
    await async_session.commit()

    async with _client(async_session) as client:
        monkeypatch.setattr(
            settings,
            "AGENT_EXECUTION_INGESTION_ENABLED",
            False,
        )
        disabled = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(token, "rollout-disabled"),
            json=_session_body("rollout-disabled"),
        )
        assert disabled.status_code == 503
        assert disabled.headers["retry-after"] == "30"

        monkeypatch.setattr(
            settings,
            "AGENT_EXECUTION_INGESTION_ENABLED",
            True,
        )
        monkeypatch.setattr(
            settings,
            "AGENT_EXECUTION_RUNTIME_ALLOWLIST",
            [runtime_id],
        )
        allowed = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(token, "rollout-allowed"),
            json=_session_body("rollout-allowed"),
        )
        assert allowed.status_code == 201, allowed.text

        denied = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(other_token, "rollout-denied"),
            json=_session_body("rollout-denied"),
        )
        assert denied.status_code == 403

        # Backout blocks new starts but lets already accepted work reach a
        # terminal state, preserving the domain row and its Outbox evidence.
        monkeypatch.setattr(
            settings,
            "AGENT_EXECUTION_INGESTION_ENABLED",
            False,
        )
        completed = await client.post(
            (
                "/api/v2/reporter/sessions/"
                f"{allowed.json()['session_public_id']}/complete"
            ),
            headers=_reporter_headers(token, "rollout-complete"),
            json={
                "ended_at": (NOW + timedelta(seconds=5)).isoformat(),
                "status": "ENDED",
                "run_count": 0,
                "error_count": 0,
            },
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "ENDED"

    session_row = await async_session.scalar(
        select(AgentSession).where(
            AgentSession.public_id == allowed.json()["session_public_id"]
        )
    )
    assert session_row is not None
    assert session_row.status == AgentSessionStatus.ENDED
    session_events = (
        await async_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_public_id == session_row.public_id
            )
        )
    ).scalars().all()
    assert {event.event_type for event in session_events} == {
        "AgentSessionStarted",
        "AgentSessionCompleted",
    }


async def test_reporter_identity_is_credential_derived_and_forgery_is_audited(
    async_session,
) -> None:
    owner = await _user(async_session, "identity-owner")
    namespace, runtime = await _tenant(
        async_session,
        owner,
        "identity-tenant",
    )
    credential, token = await _reporter_credential(
        async_session,
        runtime=runtime,
        user=owner,
        suffix="identity",
    )
    namespace_id = namespace.id
    runtime_id = runtime.id
    credential_id = credential.id
    await async_session.commit()

    async with _client(async_session) as client:
        started = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(token, "session-start-identity"),
            json=_session_body(),
        )
        assert started.status_code == 201, started.text
        payload = started.json()
        assert payload["namespace_id"] == str(namespace_id)
        assert payload["runtime_instance_id"] == str(runtime_id)
        assert payload["session_public_id"].startswith("ses_")
        assert started.headers["location"].endswith(
            payload["session_public_id"]
        )

        forged_body = {
            **_session_body("session-forged"),
            "namespace_id": namespace_id + 1000,
            "runtime_instance_id": runtime_id + 1000,
        }
        forged = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(token, "session-start-forged"),
            json=forged_body,
        )
        assert forged.status_code == 422

        oversized = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(token, "session-start-oversized"),
            json={
                **_session_body("session-oversized"),
                "prompt": "private-content-" * 5000,
            },
        )
        assert oversized.status_code == 413
        assert "private-content" not in oversized.text

    audit_row = (
        await async_session.execute(
            select(AuditLog).where(
                AuditLog.action
                == "agent_execution.identity_forgery_rejected"
            )
        )
    ).scalar_one()
    assert audit_row.namespace_id == namespace_id
    assert audit_row.resource_id == credential_id
    assert audit_row.details["rejected_fields"] == [
        "namespace_id",
        "runtime_instance_id",
    ]
    serialized_audit = str(audit_row.details)
    assert token not in serialized_audit
    assert "session-forged" not in serialized_audit
    assert await async_session.scalar(
        select(ReporterCredential.last_used_at).where(
            ReporterCredential.id == credential_id
        )
    ) is None


async def test_execution_scope_is_required_and_legacy_report_tokens_are_rejected(
    async_session,
) -> None:
    owner = await _user(async_session, "scope-owner")
    _, runtime = await _tenant(async_session, owner, "scope-tenant")
    _, missing_scope_token = await _reporter_credential(
        async_session,
        runtime=runtime,
        user=owner,
        suffix="noscope",
        scopes=["report.structured"],
    )
    disabled_runtime = RuntimeInstance(
        namespace_id=runtime.namespace_id,
        provider=RuntimeProvider.CUSTOM,
        name="disabled-execution-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.DISABLED,
    )
    async_session.add(disabled_runtime)
    await async_session.flush()
    _, disabled_runtime_token = await _reporter_credential(
        async_session,
        runtime=disabled_runtime,
        user=owner,
        suffix="disabled",
    )
    legacy_secret = "legacy-execution-secret"
    legacy = RuntimeReportToken(
        runtime_id=runtime.id,
        name="Legacy execution token",
        token_prefix="legacyv2",
        token_hash=hash_password(legacy_secret),
    )
    async_session.add(legacy)
    await async_session.commit()

    async with _client(async_session) as client:
        missing_scope = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(
                missing_scope_token,
                "missing-scope-session",
            ),
            json=_session_body("missing-scope"),
        )
        legacy_response = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(
                f"dkr_report_legacyv2_{legacy_secret}",
                "legacy-token-session",
            ),
            json=_session_body("legacy-token"),
        )
        disabled_response = await client.post(
            "/api/v2/reporter/sessions",
            headers=_reporter_headers(
                disabled_runtime_token,
                "disabled-runtime-session",
            ),
            json=_session_body("disabled-runtime"),
        )
    assert missing_scope.status_code == 403
    assert legacy_response.status_code == 403
    assert disabled_response.status_code == 403


async def test_reporter_trust_needs_a_successful_verifier_and_client_claim_is_audited(
    async_session,
) -> None:
    class Verifier:
        def __init__(self, result: bool):
            self.result = result

        async def verify(self, proof: dict) -> bool:
            return self.result and proof == {"proof": "valid"}

    bearer = await derive_reporter_trust()
    failed = await derive_reporter_trust(
        attestation_proof={"proof": "invalid"},
        verifier=Verifier(False),
    )
    verified = await derive_reporter_trust(
        attestation_proof={"proof": "valid"},
        verifier=Verifier(True),
    )
    assert bearer.trust_level == TrustLevel.CHANNEL_AUTHENTICATED
    assert bearer.trust_source == TrustSource.REPORTER
    assert failed.trust_level == TrustLevel.CHANNEL_AUTHENTICATED
    assert failed.degraded is True
    assert verified.trust_level == TrustLevel.PRODUCER_ATTESTED

    owner = await _user(async_session, "trust-owner")
    namespace, runtime = await _tenant(async_session, owner, "trust-tenant")
    _, token = await _reporter_credential(
        async_session,
        runtime=runtime,
        user=owner,
        suffix="trust",
    )
    namespace_id = namespace.id
    await async_session.commit()

    async with _client(async_session) as client:
        valid = await client.post(
            "/api/v2/reporter/runs",
            headers=_reporter_headers(token, "valid-trust-run"),
            json=_run_body("valid-trust-run"),
        )
        assert valid.status_code == 201, valid.text
        assert valid.json()["trust_level"] == "CHANNEL_AUTHENTICATED"
        assert valid.json()["trust_source"] == "REPORTER"

        forged = await client.post(
            "/api/v2/reporter/runs",
            headers=_reporter_headers(token, "forged-trust-run"),
            json={
                **_run_body("forged-trust-run"),
                "trust_level": "PRODUCER_ATTESTED",
            },
        )
        assert forged.status_code == 422

    degraded = (
        await async_session.execute(
            select(AuditLog).where(
                AuditLog.action == "agent_run.trust_degraded"
            )
        )
    ).scalar_one()
    assert degraded.namespace_id == namespace_id
    assert degraded.details["requested_trust_level"] == "PRODUCER_ATTESTED"
    assert degraded.details["effective_trust_level"] == (
        "CHANNEL_AUTHENTICATED"
    )


async def test_reporter_lifecycle_replay_conflict_and_scoped_completion(
    async_session,
) -> None:
    owner = await _user(async_session, "lifecycle-owner")
    _, runtime = await _tenant(async_session, owner, "lifecycle-tenant")
    _, token = await _reporter_credential(
        async_session,
        runtime=runtime,
        user=owner,
        suffix="lifecycle",
    )
    other_owner = await _user(async_session, "lifecycle-other")
    _, other_runtime = await _tenant(
        async_session,
        other_owner,
        "lifecycle-other-tenant",
    )
    _, other_token = await _reporter_credential(
        async_session,
        runtime=other_runtime,
        user=other_owner,
        suffix="lifecycleother",
    )
    await async_session.commit()

    async with _client(async_session) as client:
        first = await client.post(
            "/api/v2/reporter/runs",
            headers=_reporter_headers(token, "run-start-replay"),
            json=_run_body(),
        )
        replay = await client.post(
            "/api/v2/reporter/runs",
            headers=_reporter_headers(token, "run-start-replay"),
            json=_run_body(),
        )
        assert first.status_code == replay.status_code == 201
        run_public_id = first.json()["run_public_id"]
        assert replay.json()["run_public_id"] == run_public_id

        conflict = await client.post(
            "/api/v2/reporter/runs",
            headers=_reporter_headers(token, "run-start-replay"),
            json=_run_body("changed-run"),
        )
        assert conflict.status_code == 409

        foreign_complete = await client.post(
            f"/api/v2/reporter/runs/{run_public_id}/complete",
            headers=_reporter_headers(
                other_token,
                "foreign-run-complete",
            ),
            json={
                "ended_at": (NOW + timedelta(seconds=2)).isoformat(),
                "status": "SUCCEEDED",
                "duration_ms": 2000,
            },
        )
        assert foreign_complete.status_code == 404

        invalid_time = await client.post(
            f"/api/v2/reporter/runs/{run_public_id}/complete",
            headers=_reporter_headers(token, "invalid-run-complete"),
            json={
                "ended_at": (NOW + timedelta(seconds=2)).isoformat(),
                "status": "SUCCEEDED",
                "duration_ms": 1999,
            },
        )
        assert invalid_time.status_code == 409

        completed = await client.post(
            f"/api/v2/reporter/runs/{run_public_id}/complete",
            headers=_reporter_headers(token, "valid-run-complete"),
            json={
                "ended_at": (NOW + timedelta(seconds=2)).isoformat(),
                "status": "SUCCEEDED",
                "duration_ms": 2000,
            },
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "SUCCEEDED"

    actions = (
        await async_session.execute(
            select(AuditLog.action).where(
                AuditLog.action.in_(
                    (
                        "agent_execution.idempotency_conflict",
                        "agent_execution.invalid_transition",
                    )
                )
            )
        )
    ).scalars().all()
    assert "agent_execution.idempotency_conflict" in actions
    assert "agent_execution.invalid_transition" in actions


async def _seed_run(
    db,
    *,
    namespace: Namespace,
    runtime: RuntimeInstance,
    suffix: str,
    started_at: datetime,
):
    return await start_run(
        db,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key=f"query-start-{suffix}",
        envelope=RunStartEnvelope(
            external_run_id=f"query-run-{suffix}",
            started_at=started_at,
            source_schema="duckdock-run-envelope",
            source_schema_version="1.0",
            content_capture_mode="metadata_only",
            metadata={"service.name": "query-test"},
        ),
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )


async def test_management_reads_are_bounded_paginated_and_tenant_safe(
    async_session,
) -> None:
    owner = await _user(async_session, "query-owner")
    namespace, runtime = await _tenant(async_session, owner, "query-tenant")
    outsider = await _user(async_session, "query-outsider")
    other_owner = await _user(async_session, "query-other-owner")
    other_namespace, other_runtime = await _tenant(
        async_session,
        other_owner,
        "query-other-tenant",
    )
    newest = await _seed_run(
        async_session,
        namespace=namespace,
        runtime=runtime,
        suffix="newest",
        started_at=NOW + timedelta(minutes=2),
    )
    middle = await _seed_run(
        async_session,
        namespace=namespace,
        runtime=runtime,
        suffix="middle",
        started_at=NOW + timedelta(minutes=1),
    )
    await _seed_run(
        async_session,
        namespace=namespace,
        runtime=runtime,
        suffix="outside-window",
        started_at=NOW - timedelta(days=2),
    )
    foreign = await _seed_run(
        async_session,
        namespace=other_namespace,
        runtime=other_runtime,
        suffix="foreign",
        started_at=NOW + timedelta(minutes=3),
    )
    namespace_id = namespace.id
    runtime_id = runtime.id
    owner_id = owner.id
    outsider_id = outsider.id
    newest_public_id = newest.public_id
    middle_public_id = middle.public_id
    foreign_public_id = foreign.public_id
    await async_session.commit()

    params = {
        "namespace_id": namespace_id,
        "started_after": (NOW - timedelta(hours=1)).isoformat(),
        "started_before": (NOW + timedelta(hours=1)).isoformat(),
        "limit": 1,
    }
    owner_headers = {
        "Authorization": f"Bearer {create_access_token(owner_id)}"
    }
    outsider_headers = {
        "Authorization": f"Bearer {create_access_token(outsider_id)}"
    }

    async with _client(async_session) as client:
        first_page = await client.get(
            "/api/v2/agent-runs",
            params=params,
            headers=owner_headers,
        )
        assert first_page.status_code == 200, first_page.text
        first_payload = first_page.json()
        assert [item["run_public_id"] for item in first_payload["items"]] == [
            newest_public_id
        ]
        assert first_payload["next_cursor"]

        second_page = await client.get(
            "/api/v2/agent-runs",
            params={
                **params,
                "cursor": first_payload["next_cursor"],
            },
            headers=owner_headers,
        )
        assert second_page.status_code == 200, second_page.text
        assert [
            item["run_public_id"]
            for item in second_page.json()["items"]
        ] == [middle_public_id]
        assert second_page.json()["next_cursor"] is None

        changed_filter_cursor = await client.get(
            "/api/v2/agent-runs",
            params={
                **params,
                "runtime_instance_id": runtime_id,
                "cursor": first_payload["next_cursor"],
            },
            headers=owner_headers,
        )
        assert changed_filter_cursor.status_code == 422

        too_wide = await client.get(
            "/api/v2/agent-runs",
            params={
                "namespace_id": namespace_id,
                "started_after": (NOW - timedelta(days=40)).isoformat(),
                "started_before": NOW.isoformat(),
            },
            headers=owner_headers,
        )
        assert too_wide.status_code == 422

        denied_list = await client.get(
            "/api/v2/agent-runs",
            params=params,
            headers=outsider_headers,
        )
        assert denied_list.status_code == 403

        own_detail = await client.get(
            f"/api/v2/agent-runs/{newest_public_id}",
            headers=owner_headers,
        )
        assert own_detail.status_code == 200
        detail = own_detail.json()
        assert detail["namespace_id"] == str(namespace_id)
        assert "start_idempotency_key" not in detail
        assert "start_envelope_sha256" not in detail

        hidden_foreign = await client.get(
            f"/api/v2/agent-runs/{foreign_public_id}",
            headers=owner_headers,
        )
        unknown = await client.get(
            "/api/v2/agent-runs/run_unknown",
            headers=owner_headers,
        )
        assert hidden_foreign.status_code == unknown.status_code == 404
