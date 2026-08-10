# Implementation Plan: DuckDock 2.0 Foundation Implementation Phase

**Spec**: `specs/008-duckdock-2-foundation/spec.md`

**Status**: G0 approved; S1-D tenant contract implemented; remaining Foundation work in progress
**Prepared**: 2026-07-17

## 1. Phase Outcome

S0 仅完成 Architecture Gate 所需的规格、ADR、数据模型、契约和测试计划。本实现计划在 G0 通过后进入 S1-S2；完成后，DuckDock 具备一个不依赖特定观测厂商的 Agent 运行控制平面基础：每个核心实体有明确租户归属；每次执行可绑定不可变 Deployment revision；Reporter 可幂等登记 metadata-only Session/Run；领域事件通过事务 Outbox 可靠扩展；Telemetry/Trajectory/Attestation 通过 Port 隔离。

这不是“把 Langfuse 做进 DuckDock”，也不是完整评测平台。Foundation implementation 的交付标准是领域边界、数据库约束、可信写入和迁移安全可验证。

## 2. Technical Context

- **Backend**: FastAPI, SQLAlchemy, Alembic, Pydantic, Celery
- **Business database**: MySQL only
- **Artifact storage**: MinIO (metadata in MySQL, bytes in object storage)
- **Optional telemetry**: OpenTelemetry Collector and provider backends under optional `observability` profile
- **Authentication**: Existing user auth/RBAC for management APIs; `ReporterCredential` for Runtime ingestion
- **Testing**: Pytest unit/integration plus real-MySQL migration and constraint lane
- **Compatibility**: Existing WorkTrace, structured report, Clinic evaluation and Release Gate remain operational

## 3. Constitution Check

| Constraint | Foundation implementation treatment | Gate |
|---|---|---|
| MySQL is the only business primary store | Only governance metadata and indexes are added to MySQL | PASS |
| Observability databases are optional | No ClickHouse/Postgres dependency in core or default Compose | PASS |
| Collection is push-only | Reporter pushes Session/Run envelopes; no runtime scraping | PASS |
| Collection is idempotent and retryable | Canonical payload hashes, unique keys and transactional outbox | PASS |
| Audit/evidence is non-bypassable | Cross-tenant attempts, conflicts and trust changes are audited | PASS |
| Sensitive content hidden by default | Foundation implementation accepts metadata-only envelopes | PASS |
| Credentials are protected | Provider records contain only credential references | PASS |
| LLM output is advisory | No automatic gate or approval based on model output | PASS |
| Behavior changes require tests | Each implementation task starts with a failing regression test | REQUIRED |
| Real MySQL coverage | Migration, locking, unique constraints and outbox tested on MySQL | REQUIRED |

## 4. Target Architecture

```text
Agent Harness (OpenClaw, Hermes, custom)
  |-- OpenInference / OTel SDK --OTLP--> OTel Collector
  |                                      | auth and trusted mapping
  |                                      | redaction / normalization
  |                                      | filtering / sampling / routing
  |                                      +--> Langfuse or another trace sink
  |
  |-- DuckDock Reporter --> metadata-only Session/Run envelope
  |                          |
  |                          v
  |                    FastAPI control plane
  |                          |
  |            +-------------+----------------+
  |            |                              |
  |            v                              v
  |       MySQL transaction              Audit chain
  |   deployment/run indexes + outbox
  |            |
  |            v
  |       Outbox dispatcher --> Celery projections/evaluation (later)
  |
  +-- optional ATIF artifact --> MinIO (codec/import in later sprint)

DuckDock management UI/API reads MySQL governance metadata and provider-neutral
TraceBackendRef links. It does not query raw spans in request hot paths.
```

## 5. Component Boundaries

### DuckDock core

Owns Namespace, Runtime, Asset, immutable Deployment revisions, Session/Run indexes, artifact references, provider configuration references, responsibility, evidence, audit and release decisions.

### OpenTelemetry Collector

Owns telemetry receiver authentication, trusted resource attributes, redaction, normalization, filtering, sampling and routing. It does not own DuckDock tenant membership, asset ownership or release decisions.

### OpenInference / OTel GenAI semantic conventions

Provide external telemetry vocabulary. An adapter normalizes versioned external attributes into DuckDock's small stable index; their attribute names do not become database columns by default.

### Langfuse or another trace backend

Stores/query raw traces, sessions and scores and provides a trace UI. It is optional and replaceable. DuckDock stores only `TelemetrySink` and `TraceBackendRef`.

### ATIF / trajectory codec

Represents portable offline trajectory artifacts. Live traffic remains OTLP; artifact bytes go to MinIO. Foundation implementation defines the Port and artifact index only.

### DeepEval or another evaluator

Runs asynchronously behind a future `EvaluationEnginePort`. It does not own datasets, users, approvals or release gates. No evaluator is implemented in Foundation implementation.

## 6. Work Packages

### WP0 - Characterization and safety net

1. Snapshot existing WorkTrace, structured report and Release Gate behavior with regression tests.
2. Add architecture guards that fail if raw content fields/tables or vendor SDK imports enter the core package.
3. Add populated MySQL migration fixtures representing deterministic and ambiguous tenant relations.

**Exit**: Existing behavior has tests that fail on accidental replacement or tenant leakage.

### WP1 - Tenant ownership expand/backfill/contract

1. Expand migration adds nullable indexed `namespace_id` to the five existing entities.
2. Tenant resolver performs only deterministic relationship-based resolution.
3. Admin audit command reports resolved/unresolved/conflict records in JSON and human-readable form.
4. Services require Namespace for every new write immediately after expand.
5. Contract migration verifies zero unresolved/conflict rows, then adds non-null and tenant-consistency constraints where supported.

**Exit**: Fresh and remediated populated databases upgrade to head; ambiguous data blocks contract safely.

### WP2 - Deployment inventory

1. Add AgentDeployment and DeploymentComponent models, schemas and repositories.
2. Add lifecycle service for register, activate and retire.
3. Enforce same-tenant references and immutability after activation.
4. Add management endpoints using existing Namespace RBAC.

**Exit**: A new revision is required for every material component/config change.

### WP3 - Session/Run ingestion

1. Add AgentSession and AgentRun models and state transitions.
2. Add canonical envelope encoder/hash utility.
3. Resolve ReporterCredential -> Runtime -> Namespace on the server.
4. Implement start session, start run and complete run endpoints.
5. Implement tenant-scoped Run list/detail endpoints.
6. Reject content-bearing and non-allowlisted fields.

**Exit**: Trusted, metadata-only, idempotent lifecycle works with concurrency tests.

### WP4 - Artifacts, providers and trace references

1. Add AgentRunArtifact, TelemetrySink and TraceBackendRef models.
2. Define provider-neutral Python Protocol/ABC interfaces.
3. Provide null/in-memory adapters for contract tests.
4. Store credential references only and validate endpoint configuration without synchronous network calls.

**Exit**: Core behavior works with no provider configured, and a fake provider can resolve a trace link without vendor imports.

### WP5 - Transactional outbox

1. Add OutboxEvent model and event serializer allowlist.
2. Write events in the same Unit of Work as domain mutations.
3. Add concurrent-safe dispatcher, exponential backoff and consumer dedupe contract.
4. Add operational metrics/logs containing event IDs but no sensitive payload.

**Exit**: Failure injection proves no committed Run without its corresponding event.

### WP6 - Release seam and operational handoff

1. Add a provider-neutral evidence lookup seam for future candidate-pinned evaluations without changing current Gate results.
2. Document migration, rollback, alerting and optional provider rollout.
3. Run full backend, real-MySQL and default-compose regression suites.

**Exit**: Current Gate stays byte-for-byte compatible for characterized cases; future implementation cannot accidentally rely on latest Namespace evaluation without changing a failing test.

## 7. Anticipated Implementation Map

Exact filenames may follow existing repository conventions; keep ownership separated as follows:

```text
backend/app/
  models/
    agent_deployment.py
    agent_run.py
    telemetry_sink.py
    outbox_event.py
  schemas/
    agent_deployment.py
    agent_run.py
    telemetry_sink.py
  services/
    tenant_resolution_service.py
    agent_deployment_service.py
    agent_run_service.py
    outbox_service.py
  ports/
    telemetry_sink.py
    trajectory_codec.py
    attestation_verifier.py
  adapters/
    telemetry/null_sink.py
    trajectory/null_codec.py
    attestation/null_verifier.py
  api/v2/
    control_plane_runs.py
    agent_runs.py
    telemetry_sinks.py
  workers/
    outbox_dispatcher.py
  commands/
    tenant_backfill.py
backend/alembic/versions/
  *_foundation_tenant_expand.py
  *_foundation_runtime_entities.py
  *_foundation_tenant_contract.py
backend/tests/
  foundation/
  integration/
  mysql/
```

Avoid a generic `providers.py` that mixes domain operations, network clients and credentials. Ports live inward; concrete adapters live outward.

## 8. Transaction and Concurrency Design

- Session/Run mutation and Outbox insert share one SQLAlchemy transaction.
- Start requests first normalize validated Pydantic data, then hash canonical JSON.
- Unique constraints provide the final concurrency guard; service-level prechecks only improve errors.
- On duplicate key, reload the existing record and compare hashes. Equal means idempotent success; unequal means domain conflict.
- Complete uses a conditional update from `STARTED` plus stored completion hash. A lost race is reloaded and classified as replay or conflict.
- Dispatcher claims a bounded batch with `SELECT ... FOR UPDATE SKIP LOCKED`, commits the lease, dispatches outside the claim transaction, then records success/failure.
- Every consumer uses immutable `event_id` as its dedupe key.

## 9. Migration Strategy

### Phase A: Expand

- Add nullable columns, foreign keys and non-unique indexes without changing old reads.
- Deploy code that always writes Namespace on new/updated records.
- Add new tables with non-null Namespace from day one.

### Phase B: Backfill and audit

- Run deterministic resolver in batches with checkpointing.
- Emit a report with source rule and conflict candidates for every unresolved row.
- Repair data through an explicit admin workflow; never use a global default Namespace.
- Re-run until the audit reports zero unresolved/conflict.

### Phase C: Contract

- Contract migration runs preflight SQL and aborts with counts and remediation command if unsafe.
- Set Namespace non-null, add tenant-scoped unique indexes and same-tenant application invariants.
- Remove transitional fallback reads only after one successful release window.

Rollback never deletes backfilled Namespace values or new Run records. Roll back application routing first; schema contraction requires a separate approved change.

## 10. API and Schema Strategy

- Public identifiers are opaque UUID/ULID-style strings; internal numeric IDs never authorize access.
- API schemas use strict unknown-field rejection for Reporter envelopes.
- Management list endpoints require time bounds and pagination.
- Provider-neutral enums are stored as stable strings with explicit unknown handling.
- `metadata_json` is allowlisted and size limited; it cannot become a generic raw event dump.
- Foundation contracts live under `contracts/` as Draft 2020-12 JSON Schema plus validated examples. API examples and later OpenAPI snapshots must reference these contracts instead of duplicating incompatible payload definitions.

## 11. Test Strategy

### Test-first sequence

Each work package begins with a failing test and commits the smallest implementation that makes it pass. Behavior changes without tests fail review.

### Unit tests

- Canonicalization and SHA-256 stability
- State machines and immutability
- Tenant resolution rules and conflict classification
- Payload allowlist and content rejection
- Provider Port contract tests
- Outbox event payload redaction

### Integration tests

- ReporterCredential-derived tenant/runtime
- RBAC and cross-tenant enumeration resistance
- Idempotent replay and 409 conflicts
- WorkTrace optional linkage without semantic replacement
- Audit events on conflict and trust changes
- Provider absent/unavailable behavior

### Real MySQL tests

- Expand/backfill/contract on empty and populated fixtures
- Foreign key and composite unique behavior
- Concurrent start/complete races
- `SKIP LOCKED` dispatcher workers
- Transaction rollback when Outbox insert fails

### Regression tests

- Structured report still creates WorkTrace as before
- Current Release Gate decisions unchanged
- Default Compose does not start observability services
- Import/compile checks work without vendor SDKs installed

## 12. Observability of the Foundation

Application metrics/logs should expose only operational metadata:

- accepted/replayed/conflicted Run envelopes by Runtime public ID hash and status
- tenant backfill resolved/unresolved/conflict counts
- Outbox pending age, attempts, published and failed counts
- TraceBackendRef pending/confirmed/error counts
- provider health state without secret or endpoint query parameters

No metric label may contain prompt, user text, external session ID or full trace content.

## 13. Rollout and Backout

1. Ship characterization tests and expand migration.
2. Deploy dual-compatible code that writes Namespace and new entities but leaves new ingestion route feature-flagged.
3. Run backfill audit in staging, then production; resolve conflicts.
4. Apply contract migration only after signed audit evidence.
5. Enable Reporter Run ingestion for an allowlist of Runtime IDs.
6. Enable Outbox dispatcher with a small batch and monitor pending age/retries.
7. Enable management reads; keep Telemetry providers optional.

Backout order: disable ingestion flag, stop dispatcher, revert application routing, keep additive tables/columns intact. Never delete Run/Outbox evidence during an incident rollback.

## 14. Architecture Decision Log

| ID | Decision | Rationale | Status |
|---|---|---|---|
| FND-ADR-001 | `WorkTrace != AgentRun` | 管理摘要和执行事实具有不同粒度、信任与保留策略 | Accepted |
| FND-ADR-002 | MySQL stores governance metadata only | 避免高吞吐、高基数 trace 破坏业务库 | Accepted |
| FND-ADR-003 | OTLP live, ATIF archive | 分离实时遥测和可移植离线制品 | Accepted |
| FND-ADR-004 | External semantics pass through versioned normalization | OTel GenAI/OpenInference 正在演进，不能固化为 DB schema | Accepted |
| FND-ADR-005 | Langfuse is an optional adapter | 避免 trace provider 成为治理事实源 | Accepted |
| FND-ADR-006 | DeepEval is an execution adapter, not the evaluation system of record | Dataset、审批、版本和 Gate 语义必须留在 DuckDock | Accepted |
| FND-ADR-007 | Collector is the telemetry trust boundary | Harness 提供的 tenant/runtime attrs 不可信 | Accepted |
| FND-ADR-008 | Metadata-only is the default and only Foundation implementation mode | 最小化敏感数据与合规风险 | Accepted |
| FND-ADR-009 | Domain changes write a transactional outbox | 消除 DB commit 与异步投递之间的丢失窗口 | Accepted |
| FND-ADR-010 | Future gates use candidate-pinned immutable evidence | “最新评测”无法证明当前候选版本 | Accepted; implemented by EH-05/revision 0044 |

## 15. Definition of Done

- P0 tasks in `tasks.md` completed and independently reviewable。
- Fresh and populated real-MySQL migration lanes pass。
- Cross-tenant, idempotency, transaction atomicity and content-rejection tests pass。
- No vendor SDK is required to import or start core API。
- No raw trace/content table or column is added to MySQL。
- Existing WorkTrace and Release Gate characterization tests pass。
- Rollout/backout and unresolved-tenant remediation have been exercised in staging。
