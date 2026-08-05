# Quickstart: Implement and Verify the Foundation Phase

This guide describes the implemented Foundation workflow. Reproducible
acceptance results and the local staging-equivalent backout record are in
[`evidence/g1-evidence-alpha-20260728.md`](evidence/g1-evidence-alpha-20260728.md).

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

The Reporter credential must include the `execution.write` scope. Newly
enrolled and rotated credentials receive it automatically; legacy
`RuntimeReportToken` values are not accepted by `/api/v2/reporter/*`.

New Session/Run starts are rollout controlled:

```text
AGENT_EXECUTION_INGESTION_ENABLED=false
AGENT_EXECUTION_RUNTIME_ALLOWLIST=[]
```

The application-safe default is disabled. The development Compose file enables
ingestion when the variable is absent so the local API remains immediately
usable; production should set the switch explicitly and may start with a JSON
or comma-separated Runtime ID allowlist. A disabled start returns HTTP 503 with
`Retry-After: 30`; a Runtime outside a non-empty allowlist returns HTTP 403.
Completion endpoints remain available during backout.

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

Management list reads require a timezone-aware window no wider than 31 days:

```text
GET /api/v2/agent-runs?namespace_id=7&started_after=2026-07-01T00:00:00Z&started_before=2026-08-01T00:00:00Z&limit=50
Authorization: Bearer <user-access-token>
```

Use the returned opaque `next_cursor` with exactly the same filters for the
next page. A foreign or unknown Run public ID returns the same 404 response.

## 6. Register Artifact Metadata and a Telemetry Sink

Artifact registration stores metadata only. The `object_uri` must be a relative
internal object key or an `s3://` URI targeting DuckDock's configured bucket:

```text
POST /api/v2/agent-runs/{run_public_id}/artifacts?namespace_id=7
Authorization: Bearer <user-access-token>
Content-Type: application/json
```

```json
{
  "kind": "TRAJECTORY",
  "schema_name": "atif",
  "schema_version": "1.0",
  "object_uri": "runs/2026/07/28/trajectory.json",
  "sha256": "<64 lowercase hex characters>",
  "size_bytes": 1024,
  "sensitivity": "RESTRICTED",
  "completeness": "COMPLETE"
}
```

This request does not read, download or parse the object. Raw trajectory,
prompt, completion and tool content are rejected by the strict schema.

Telemetry Sink management accepts a credential reference, never a token:

```text
POST /api/v2/telemetry-sinks
Authorization: Bearer <user-access-token>
Content-Type: application/json
```

```json
{
  "namespace_id": 7,
  "provider": "custom",
  "name": "primary-telemetry",
  "endpoint": "https://telemetry.example.test/v1",
  "project_ref": "duckdock-dev",
  "credential_ref": "secret://duckdock/telemetry",
  "config": {
    "protocol": "otlp_http",
    "timeout_ms": 5000,
    "verify_tls": true
  }
}
```

Endpoint userinfo, private IP literals, query strings and secret-looking
configuration fields are rejected. Provider confirmation runs after Run
persistence; a provider failure only records a sanitized TraceBackendRef error.

## 7. Verify Outbox Atomicity

Run unit and real-MySQL tests:

```powershell
python -m pytest tests/foundation/test_transactional_outbox.py -q
python -m pytest tests/foundation/test_transactional_outbox_migration.py -q
python -m pytest tests/foundation/test_transactional_outbox.py -m mysql -q
```

Then inspect through an authenticated admin operation or a read-only SQL session:

- Run start has exactly one `AgentRunRegistered` event.
- Run completion has exactly one `AgentRunCompleted` event.
- Payload contains IDs, state and low-sensitive counters only.
- Failure injection rolls back both Run transition and event.
- Two dispatchers do not lease the same row concurrently.

Do not manually update Outbox status in production; use the authenticated retry operation.

System administrators can inspect payload-free health and retry an exhausted
event without changing its immutable event ID:

```text
GET /api/v2/outbox/health
POST /api/v2/outbox/{event_id}/retry
Authorization: Bearer <system-admin-access-token>
```

The retry body is `{"reason": "provider recovered"}`. Only `FAILED` events are
accepted; pending, leased and published events return 409.

## 8. Verify Optional Provider Behavior

No Sink configured:

- Deployment and Session/Run APIs succeed.
- `TraceBackendRef` may be absent or pending.
- Core import/start does not require Langfuse, DeepEval or ATIF packages.

With a fake/in-memory Sink in tests:

- A trace ID can resolve to a provider-neutral TraceBackendRef.
- Provider error changes only reference status and retry state.
- No secret is returned by API or emitted to logs.

Foundation implementation does not require a real Langfuse deployment.

Generic OTLP uses a separate optional per-Runtime bridge. Register an active
TelemetrySink and an `execution.write` ReporterCredential in the same
Runtime/Namespace, set the `DUCKDOCK_GENERIC_*` values, then start:

```powershell
docker compose --profile telemetry-generic up -d `
  otel-generic-queue-init otel-collector-generic
curl http://127.0.0.1:14135/
```

The Collector exports sanitized OTLP to the provider and to:

```text
POST /api/v2/reporter/telemetry-sinks/{sink_public_id}/v1/traces
Content-Type: application/json
Authorization: Bearer <ReporterCredential>
```

Never call this endpoint with raw Harness telemetry. The registered Collector
must first remove/replace governance attributes and apply the metadata-only
policy. Reproduce the dual-export, quarantine privacy boundary and
provider-outage replay without real credentials:

```powershell
ops/otel-collector/generic-otlp-smoke.sh
```

Pack/ATIF uses the same `execution.write` ReporterCredential boundary. Small
Packs retain the three-step single-PUT lifecycle:

```text
POST /api/v2/reporter/pack-imports
PUT  <returned MinIO presigned upload_url>
POST /api/v2/reporter/pack-imports/{public_id}/finalize
```

For resumable transfer, set `upload_mode=MULTIPART` and optionally
`multipart_part_size_bytes` (minimum 5 MiB), then use:

```text
GET  /api/v2/reporter/pack-imports/{public_id}/multipart
POST /api/v2/reporter/pack-imports/{public_id}/multipart/parts/{part_number}
PUT  <returned MinIO part upload_url>
POST /api/v2/reporter/pack-imports/{public_id}/multipart/complete
```

The GET response is the server-observed resume point. Complete accepts sorted,
contiguous part number/ETag receipts, verifies exact part sizes and then runs
the same whole-Pack SHA-256/finalize path. No Artifact becomes complete before
that final verification.

Pack adapters can submit up to 50 queue items to
`POST /api/v2/reporter/pack-import-batches`. The response identifies every
accepted/replayed/retryable/rejected item. Server-side receipts survive restart
and the returned `ack_cursor` advances only across contiguous persisted
receipts; the adapter must persist the response before moving its local cursor.
The cursor is also readable at
`GET /api/v2/reporter/pack-import-batches/{stream_id}/cursor`.

`POST /api/v2/reporter/pack-exports` creates an immutable, checksum-indexed
ATIF Pack from verified metadata-only Run trajectory artifacts.
`POST /api/v2/reporter/evaluation-replays` validates the referenced evaluation
artifact bytes against their stored size/SHA and emits one idempotent
`EvaluationResultReplayed` Outbox event. Evaluation execution/domain models
remain deferred to NEXT-004.

The create body contains `expected_pack_sha256`, `expected_size_bytes` and a
strict `duckdock-pack/1.0` manifest. The ZIP must contain exactly
`manifest.json` plus the declared relative payload paths. Finalize verifies the
whole Pack, the embedded canonical manifest, every payload checksum and ATIF
version before linking any Artifact or Outbox event. The default bounds are
64 MiB compressed, 256 MiB uncompressed, 64 payloads, 64 MiB per entry, 100:1
compression ratio and a 128 KiB manifest.

Foundation recognizes ATIF v1.0–v1.7 but remains `metadata_only`: ATIF content
such as messages, reasoning, tool arguments/results or observations is
quarantined because a producer-declared receipt cannot grant Namespace content
authorization. Unknown ATIF versions are rejected, not normalized. Partial
payloads require `loss_reason` and produce `IMPORTED_PARTIAL`.

Run the conformance and live MinIO lanes:

```powershell
python -m pytest tests/foundation/test_pack_atif_import.py -q
$env:RUN_MINIO_INTEGRATION="1"
python -m pytest tests/foundation/test_pack_atif_import.py -k "real_minio" -q
```

The live tests cover both single PUT and actual MinIO multipart resume/complete.
They use exact content-addressed test keys and remove source Packs plus
extracted test payloads afterward.

## 9. Negotiate Adapter Capability and Inspect Fleet

Every supported profile sends the same strict descriptor through the
ReporterCredential channel:

```text
POST /api/v2/reporter/handshakes
Authorization: Bearer <ReporterCredential>
Content-Type: application/json
```

```json
{
  "adapter_id": "openclaw-reporter",
  "adapter_version": "1.2.0",
  "profile": "openclaw-reporter",
  "source_schema": "openclaw-run",
  "source_schema_version": "2026-07",
  "capabilities": [
    "session_control",
    "run_control",
    "otel_trace_correlation",
    "durable_replay"
  ],
  "content_capture_modes": ["metadata_only"],
  "instance_id": "adapter-local-stable-id",
  "boot_id": "per-process-random-id",
  "client_nonce": "at-least-128-bit-base64url",
  "client_time": "2026-07-30T04:00:00Z",
  "claimed_capability_level": "DD-C3"
}
```

Send the returned `handshake_id` to
`POST /api/v2/reporter/heartbeats`. Generic OTLP may also bind a projection
request with the `DuckDock-Handshake-Id` header; Pack/ATIF may put the same ID
in `manifest.producer.handshake_id`. A missing/expired/degraded or
profile-mismatched binding fails closed.

Namespace members inspect the projection at:

```text
GET /api/v2/fleet/runtimes?namespace_id=7
Authorization: Bearer <user-access-token>
```

The frontend route is `/fleet`. An unhandshaken Runtime must show `NONE` /
`NEVER` and no accepted capabilities. Run the shared suite with:

```powershell
python -m pytest tests/foundation/test_adapter_fleet.py -q
```

## 10. Run the Verification Suite

Adjust paths to match the implementation, but the final review must include all categories:

```powershell
python -m pytest tests/foundation -q
python -m pytest tests -q
python -m pytest tests -m mysql -q
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

Expected: no observability-only database, Collector, Langfuse exporter or
protocol-mock service appears unless its optional `observability`,
`telemetry`, `telemetry-langfuse` or `telemetry-test` profile is explicitly
selected. The `telemetry-generic` profile is also optional.

Validate OpenClaw DD-C3 restart behavior separately:

```powershell
ops/otel-collector/durable-replay-smoke.sh
```

The test uses the optional Collector queue volume only. Runtime MCP lifecycle
envelopes use a separate metadata-only SQLite WAL with stable sequence,
idempotency key and ack cursor; these two queues must not be reported as one
exactly-once delivery guarantee.

## 11. Backout Drill

If ingestion causes an incident:

1. Set `AGENT_EXECUTION_INGESTION_ENABLED=false`, or narrow
   `AGENT_EXECUTION_RUNTIME_ALLOWLIST`, and recreate/reload the API service.
2. Stop the Outbox dispatcher gracefully by stopping Beat (do not purge Redis
   or delete leased/PENDING Outbox rows).
3. Revert application routing to the previous release.
4. Keep additive schema, Run records and Outbox evidence intact.
5. Diagnose and replay pending events only after idempotency checks.

Do not drop tables, null tenant ownership, delete Outbox rows or rewrite terminal Run facts as part of a routine backout.

## 12. Done Checklist

- [x] WorkTrace/report and Release Gate characterization tests remain green.
- [x] Empty and populated real-MySQL migrations pass.
- [x] Ambiguous tenant history blocks contract migration safely.
- [x] All new writes are directly tenant scoped.
- [x] Deployment is immutable after activation.
- [x] Run start/complete is credential-derived, metadata-only and idempotent.
- [x] Outbox mutation is atomic and dispatcher is retry-safe.
- [x] Core runs with no optional telemetry/evaluation SDK.
- [x] Default Compose service set is unchanged.
- [x] MySQL has no raw span, prompt or tool payload storage.

These checks close the Foundation phase. The G1 performance and v1→v2
reconciliation technical lanes are also complete; the reproducible results
and exact semantic boundary are in the Evidence Alpha record.
Product/Architecture Owner approval was recorded on 2026-07-30.

## 12. Reproduce the G1 Technical Gate

The load gate exercises the complete FastAPI ASGI HTTP, Reporter
authentication, validation and real-MySQL transaction path. It refuses a
non-MySQL database or a database whose name does not contain `test` or `perf`,
requires the explicit destructive flag, and drops the isolated tables on exit:

```powershell
$env:DATABASE_URL = "mysql+aiomysql://duckdock_test:password@127.0.0.1:3306/duckdock_perf"
python scripts/g1_run_control_load_gate.py --reset-test-database --json
```

Never point this command at the normal application database. Use
`--keep-database` only while diagnosing an isolated disposable lane.

Reconciliation is event-driven and idempotent. A v1 management summary without
an execution fact is `EXPECTED_LEGACY_ONLY`; it is not converted into a
synthetic AgentRun. System administrators can inspect aggregate health and
recompute an individual WorkTrace projection through:

```text
GET  /api/v2/reconciliation/health
GET  /api/v2/reconciliation/work-traces/{work_trace_id}
POST /api/v2/reconciliation/work-traces/{work_trace_id}
```
