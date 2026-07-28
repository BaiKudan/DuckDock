# Quickstart: Implement and Verify the Foundation Phase

This guide describes the expected developer workflow once the Foundation implementation tasks begin. It is not evidence that the feature is already implemented.

## 1. Prerequisites

- Windows PowerShell or an equivalent shell
- Python version required by the repository
- Docker with a real MySQL test instance
- Existing backend dependencies installed
- `TEST_MYSQL_URL` pointing to a disposable MySQL database

Do not point migration or destructive fixture commands at production.

```powershell
Set-Location D:\formyubuntu\Project\DuckDock\backend
$env:TEST_MYSQL_URL = "mysql+pymysql://duckdock_test:password@127.0.0.1:3306/duckdock_test"
```

Use the repository's documented driver and URL form if it differs from the example.

## 2. Start with Characterization Tests

Before adding models, run or create the Foundation implementation characterization suite:

```powershell
python -m pytest tests/foundation/test_work_trace_compatibility.py -q
python -m pytest tests/foundation/test_release_gate_compatibility.py -q
python -m pytest tests/foundation/test_architecture_boundaries.py -q
```

Expected result: existing WorkTrace/report and Release Gate behavior is captured, and core imports without optional observability SDKs.

## 3. Exercise Tenant Migration Safely

The implementation must expose distinct expand and contract revisions. Use actual revision IDs from Alembic history rather than copying placeholders.

```powershell
python -m alembic history
python -m alembic upgrade <foundation_expand_revision>
python -m app.commands.tenant_backfill audit --format json --output .tmp\tenant-backfill.json
```

Review the report:

- `resolved` rows include entity ID, Namespace and deterministic rule.
- `unresolved` rows have no supported tenant evidence.
- `conflict` rows list all supported Namespace candidates.

Do not run the contract migration until unresolved/conflict counts are zero. After explicit remediation:

```powershell
python -m app.commands.tenant_backfill apply --batch-size 500
python -m app.commands.tenant_backfill audit --fail-on-unresolved
python -m alembic upgrade head
```

Required real-MySQL checks:

```powershell
python -m pytest tests/mysql/test_foundation_migrations.py -q
python -m pytest tests/mysql/test_tenant_backfill.py -q
```

An ambiguous fixture should make the contract preflight fail with counts and remediation guidance. That failure is a passing safety test, not a reason to assign a default Namespace.

## 4. Register an Immutable Deployment

The final endpoint names should follow the repository's existing router conventions. A representative request is:

```json
{
  "runtime_public_id": "rt_example",
  "agent_asset_public_id": "asset_example",
  "external_deployment_id": "openclaw-prod-agent",
  "environment": "prod",
  "revision": "2026.07.17-1",
  "configuration_digest": "<64 lowercase hex characters>",
  "components": [
    {
      "component_role": "skill",
      "skill_version_public_id": "skillv_example",
      "content_digest": "<64 lowercase hex characters>"
    }
  ]
}
```

Verify:

1. Register returns a `REGISTERED` Deployment.
2. Activate changes it to `ACTIVE` and writes an audit record.
3. Updating a component or digest now fails.
4. A material change must be registered as a new revision.

## 5. Exercise Reporter Session/Run Ingestion

Use a test Reporter token in the Authorization header. Never place real tokens in shell history or documentation.

### Start a Session

```http
POST /api/v2/reporter/sessions
Authorization: Bearer <reporter-token>
Idempotency-Key: session-start-001
Content-Type: application/json

{
  "external_session_id": "session-001",
  "deployment_public_id": "deployment-example",
  "started_at": "2026-07-17T08:00:00Z",
  "sensitivity": "RESTRICTED",
  "content_capture_mode": "metadata_only",
  "metadata": {
    "deployment.environment": "production",
    "harness.version": "1.2.0"
  }
}
```

### Start a Run

```http
POST /api/v2/reporter/runs
Authorization: Bearer <reporter-token>
Idempotency-Key: run-start-001
Content-Type: application/json

{
  "external_run_id": "run-001",
  "session_public_id": "session-public-id-returned-by-start",
  "deployment_public_id": "deployment-example",
  "otel_trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "root_span_id": "00f067aa0ba902b7",
  "attempt": 1,
  "source_schema": "duckdock-run-envelope",
  "source_schema_version": "1.0",
  "started_at": "2026-07-17T08:00:05Z",
  "content_capture_mode": "metadata_only"
}
```

### Complete the Run

```http
POST /api/v2/reporter/runs/{run_public_id}/complete
Authorization: Bearer <reporter-token>
Idempotency-Key: run-complete-001
Content-Type: application/json

{
  "status": "SUCCEEDED",
  "ended_at": "2026-07-17T08:00:25Z",
  "duration_ms": 20000,
  "step_count": 4,
  "model_call_count": 2,
  "tool_call_count": 1,
  "input_token_count": 1200,
  "output_token_count": 240
}
```

### Complete the Session

```http
POST /api/v2/reporter/sessions/{session_public_id}/complete
Authorization: Bearer <reporter-token>
Idempotency-Key: session-complete-001
Content-Type: application/json

{
  "status": "ENDED",
  "ended_at": "2026-07-17T08:00:26Z",
  "run_count": 1,
  "error_count": 0
}
```

Verify the trust boundary:

- Database Runtime and Namespace come from ReporterCredential.
- Adding `namespace_id`, `runtime_id` or `runtime_instance_id` is rejected by the strict schema and audited as an identity-forgery attempt.
- A valid bearer token yields `trust_level=CHANNEL_AUTHENTICATED` and `trust_source=REPORTER`; it does not imply producer attestation.
- Repeating the same request/key returns the same resource.
- Reusing the key with changed content returns 409.
- Adding `prompt`, `messages`, `tool_arguments` or `tool_result` returns 422.

## 6. Verify Outbox Atomicity

Run unit and real-MySQL tests:

```powershell
python -m pytest tests/foundation/test_outbox_payloads.py -q
python -m pytest tests/mysql/test_outbox_atomicity.py -q
python -m pytest tests/mysql/test_outbox_dispatcher_concurrency.py -q
```

Then inspect through an authenticated admin operation or a read-only SQL session:

- Run start has exactly one `AgentRunRegistered` event.
- Run completion has exactly one `AgentRunCompleted` event.
- Payload contains IDs, state and low-sensitive counters only.
- Failure injection rolls back both Run transition and event.
- Two dispatchers do not lease the same row concurrently.

Do not manually update Outbox status in production; use the authenticated retry operation.

## 7. Verify Optional Provider Behavior

No Sink configured:

- Deployment and Session/Run APIs succeed.
- `TraceBackendRef` may be absent or pending.
- Core import/start does not require Langfuse, DeepEval or ATIF packages.

With a fake/in-memory Sink in tests:

- A trace ID can resolve to a provider-neutral TraceBackendRef.
- Provider error changes only reference status and retry state.
- No secret is returned by API or emitted to logs.

Foundation implementation does not require a real Langfuse deployment.

## 8. Run the Verification Suite

Adjust paths to match the implementation, but the final review must include all categories:

```powershell
python -m pytest tests/foundation -q
python -m pytest tests/integration -q
python -m pytest tests/mysql -q
python -m ruff check app tests
python -m mypy app
python -m compileall app alembic
python -c "from app.main import app; print(app.title)"
```

Validate default Compose separately:

```powershell
Set-Location D:\formyubuntu\Project\DuckDock
docker compose config --services
```

Expected: no observability-only database, Collector or Langfuse service appears unless the optional profile is explicitly selected.

## 9. Backout Drill

If ingestion causes an incident:

1. Disable the Run ingestion feature flag or Runtime allowlist.
2. Stop the Outbox dispatcher gracefully.
3. Revert application routing to the previous release.
4. Keep additive schema, Run records and Outbox evidence intact.
5. Diagnose and replay pending events only after idempotency checks.

Do not drop tables, null tenant ownership, delete Outbox rows or rewrite terminal Run facts as part of a routine backout.

## 10. Done Checklist

- [ ] WorkTrace/report and Release Gate characterization tests remain green.
- [ ] Empty and populated real-MySQL migrations pass.
- [ ] Ambiguous tenant history blocks contract migration safely.
- [ ] All new writes are directly tenant scoped.
- [ ] Deployment is immutable after activation.
- [ ] Run start/complete is credential-derived, metadata-only and idempotent.
- [ ] Outbox mutation is atomic and dispatcher is retry-safe.
- [ ] Core runs with no optional telemetry/evaluation SDK.
- [ ] Default Compose service set is unchanged.
- [ ] MySQL has no raw span, prompt or tool payload storage.
