# Tasks: DuckDock 2.0 Foundation Implementation Phase

**Spec**: `specs/008-duckdock-2-foundation/spec.md`

**Plan**: `specs/008-duckdock-2-foundation/plan.md`

All behavior work follows **red -> green -> refactor**. P0 is a hard gate: do not begin P1 integration work while any P0 test, migration check or tenant invariant is failing.

The numbered implementation phases below run across global S1-S2 after G0. “Phase 0” is a characterization work package, not global Sprint 0.

## Dependency Order

```text
FND-001..006 characterization
        |
        v
FND-010..019 tenant expand/backfill/contract
        |
        +-----------> FND-020..028 deployment inventory
        |                         |
        +-----------> FND-030..043 session/run ingestion
                                  |
                  +---------------+---------------+
                  v                               v
             FND-050..056                    FND-060..069
             provider/artifact               transactional outbox
                  +---------------+---------------+
                                  v
                             FND-070..078
                             rollout and gates
```

## Phase 0 - Characterization and Architecture Guards (P0)

- [x] **FND-001 [TEST]** Add a regression test that captures current structured-report creation of `WorkTrace`, including dedupe behavior and key fields. The test must fail if AgentRun silently replaces WorkTrace. Evidence: `backend/tests/foundation/test_work_trace_compatibility.py`.
- [x] **FND-002 [TEST]** Add representative Release Gate characterization tests for pass/fail/no-evaluation cases before adding any foundation seam. Evidence: `backend/tests/foundation/test_release_gate_compatibility.py`.
- [x] **FND-003 [TEST]** Add cross-tenant repository characterization tests for Runtime, Asset, Binding, WorkTrace and Evidence current read/write paths. Evidence: `backend/tests/foundation/test_tenant_path_compatibility.py`.
- [x] **FND-004 [TEST]** Add an architecture test that fails if models named Span/Prompt/ToolEvent or raw content columns are introduced into the MySQL domain package. Evidence: `backend/tests/foundation/test_architecture_boundaries.py`.
- [x] **FND-005 [TEST]** Add an import-boundary test that core services import and start with Langfuse, DeepEval and Harbor/ATIF packages absent. Evidence: `backend/tests/foundation/test_architecture_boundaries.py`.
- [ ] **FND-006 [DOC]** Record the ten accepted foundation decisions from `plan.md` in the implementation PR description and link each decision to its enforcing test.

**Exit gate**: FND-001 through FND-005 pass on the unchanged behavior baseline.

## Phase 1 - Direct Namespace Ownership (P0)

### Tests first

- [x] **FND-010 [TEST]** Create real-MySQL fixtures for: empty DB, fully deterministic single-tenant legacy data, unresolved data, and a Runtime bound to Assets from two Namespaces. Evidence: `test_tenant_expand_migration.py` plus `test_tenant_backfill_mysql.py`; full MySQL marker lane 10 passed, including a REPEATABLE READ CAS race.
- [x] **FND-011 [TEST]** Write failing tenant resolver tests for precedence, repeatability, unresolved classification and conflict classification. Evidence: `test_tenant_resolution_service.py`, 17 passed after real red runs for the missing module, soft-deleted historical identity, WorkTrace fallback safety and complete nested-conflict candidates.
- [x] **FND-012 [TEST]** Write failing API/service tests proving new Runtime, Asset, Binding, WorkTrace and Evidence records cannot be created without an authorized Namespace. Evidence: `test_tenant_write_enforcement.py`; management schemas require Namespace, API authorization is enforced, and the trusted ingestion/materialization builders fail closed.
- [x] **FND-013 [TEST]** Write failing tests that reject Binding/WorkTrace/Evidence relationships across Namespaces. Evidence: `test_tenant_write_enforcement.py`, handover tenant regressions, and the centralized same-tenant builders.
- [ ] **FND-014 [TEST]** Write a migration test proving contract migration aborts with actionable counts when unresolved/conflict rows remain.

### Implementation

- [x] **FND-015 [DB]** Add expand migration with nullable, indexed `namespace_id` FKs on `RuntimeInstance`, `AIAsset`, `RuntimeBinding`, `WorkTrace` and `EvidenceItem`. Evidence: revision `20260717_0027`, 8 migration tests, real MySQL populated upgrade, `upgrade head` and `alembic check`.
- [x] **FND-016 [CORE]** Implement deterministic `TenantResolutionService`; return resolution rule and candidates, never infer from Membership, names or free-form metadata. Evidence: `app/services/tenant_resolution_service.py`; 17 resolver tests and five real-MySQL S1-B fixtures.
- [x] **FND-017 [OPS]** Implement an idempotent batch backfill/audit command with JSON and console reports, checkpointing and non-zero exit on unresolved/conflict. Evidence: `app/services/tenant_backfill_service.py`, `scripts/backfill_foundation_tenants.py`, 14 service/CLI tests, mode/database/revision-bound atomic checkpoints, CAS current-read conflict handling and `tenant-backfill-runbook.md`.
- [x] **FND-018 [CORE]** Update all new-write services and tenant-scoped repositories to require Namespace and validate same-tenant relationships. Evidence: `tenant_write_service.py`; Runtime/Reporter/Asset/Handover management writes plus adapter, structured-report, analysis-materializer and handover-package write paths; frontend and Hermes pilot require an explicit Namespace.
- [ ] **FND-019 [DB]** Add contract migration preflight, non-null constraints and tenant-scoped indexes. Document the exact remediation/retry path in migration output.

**Acceptance**:

- Backfill run twice produces the same state and no duplicate audit effects.
- Real MySQL rejects unsafe contract migration and accepts it after explicit remediation.
- No service request can create or link a cross-tenant record.

## Phase 2 - Immutable Deployment Inventory (P0)

### Tests first

- [ ] **FND-020 [TEST]** Write model/service tests for `REGISTERED -> ACTIVE -> RETIRED` and `REGISTERED -> FAILED` transitions.
- [ ] **FND-021 [TEST]** Write failing tests proving ACTIVE/RETIRED Deployment identity, revision, digest, Runtime and components are immutable.
- [ ] **FND-022 [TEST]** Write failing tests for cross-tenant Runtime/Asset/SkillVersion component references and missing version/digest identity.
- [ ] **FND-023 [TEST]** Write concurrent registration tests for duplicate `(namespace, runtime, external_deployment_id, revision)`.

### Implementation

- [ ] **FND-024 [DB]** Add `AgentDeployment` and `DeploymentComponent` models/migration with constraints and indexes from `data-model.md`.
- [ ] **FND-025 [CORE]** Implement register, activate and retire services with immutable snapshot semantics and audit events.
- [ ] **FND-026 [API]** Add Namespace/RBAC-scoped register, activate, retire, list and detail endpoints using existing API conventions.
- [ ] **FND-027 [API]** Add strict Pydantic schemas and OpenAPI examples; never accept secret or raw prompt content in component configuration.
- [ ] **FND-028 [TEST]** Add end-to-end test: register revision 1, activate, reject mutation, register revision 2, retire revision 1.

**Acceptance**: Every activated deployment is an immutable, tenant-safe version snapshot.

## Phase 3 - Session/Run Metadata Ingestion (P0)

### Tests first

- [ ] **FND-030 [TEST]** Write canonical JSON tests covering key order, optional/null fields, timestamp normalization, Unicode and numeric representation.
- [ ] **FND-031 [TEST]** Write start-session tests proving Runtime/Namespace are derived from `ReporterCredential` and forged request claims are rejected/audited.
- [ ] **FND-032 [TEST]** Write start-run tests for same-key/same-hash replay, same-key/different-hash 409, and concurrent duplicate requests.
- [ ] **FND-033 [TEST]** Write complete-run tests for legal transitions, identical replay, conflicting terminal state and completion races.
- [ ] **FND-034 [TEST]** Write strict-schema tests rejecting `prompt`, `messages`, `completion`, `tool_arguments`, `tool_result` and oversized/deep metadata.
- [ ] **FND-035 [TEST]** Write trust tests proving bearer authentication yields `CHANNEL_AUTHENTICATED + REPORTER`, while only a successful verifier can yield `PRODUCER_ATTESTED`.
- [ ] **FND-036 [TEST]** Write management read tests for tenant isolation, pagination/time bounds and non-enumerable foreign IDs.

### Implementation

- [ ] **FND-037 [DB]** Add `AgentSession` and `AgentRun` models/migration with public IDs, state constraints, tenant-scoped uniqueness, hashes and aggregate fields.
- [ ] **FND-038 [CORE]** Implement validated-envelope canonicalization and SHA-256 utility; hash only after strict schema validation.
- [ ] **FND-039 [CORE]** Implement Session/Run state services and conditional terminal transition.
- [ ] **FND-040 [SEC]** Extend Reporter scope enforcement for Run ingestion and centralize credential -> Runtime -> Namespace resolution.
- [ ] **FND-041 [API]** Implement Session start, Run start and Run complete endpoints listed in `spec.md`.
- [ ] **FND-042 [API]** Implement tenant-scoped AgentRun list/detail returning metadata, version references and trace links only.
- [ ] **FND-043 [AUDIT]** Audit forged identity claims, idempotency conflicts, invalid state transitions and trust degradation without logging tokens or content.

**Acceptance**:

- Same request can be retried safely after timeout.
- Different content cannot hide behind an existing idempotency key.
- Raw execution content never reaches the persistence service.

## Phase 4 - Artifacts and Provider Ports (P0/P1)

### P0 data and boundaries

- [ ] **FND-050 [TEST]** Write failing model tests for artifact checksum/size/schema, internal object key validation and same-tenant Run relation.
- [ ] **FND-051 [TEST]** Write Port contract tests for null/in-memory TelemetrySink, TrajectoryCodec and AttestationVerifier adapters.
- [ ] **FND-052 [DB]** Add `AgentRunArtifact`, `TelemetrySink` and `TraceBackendRef` models/migration with constraints from `data-model.md`.
- [ ] **FND-053 [CORE]** Define inward-facing `TelemetrySinkPort`, `TrajectoryCodecPort` and `AttestationVerifierPort`; type them without vendor classes.
- [ ] **FND-054 [CORE]** Implement null adapters so the core works with no optional provider packages or configuration.
- [ ] **FND-055 [SEC]** Validate Telemetry Sink endpoint/config and persist `credential_ref` only; add a regression test that secret-looking fields are rejected.
- [ ] **FND-056 [CORE]** Add AgentRunArtifact metadata registration service; defer object parsing and ATIF validation.

### P1 optional management surface

- [ ] **FND-057 [API]** Add RBAC-scoped TelemetrySink create/list/update/disable endpoints if required by the existing admin UX; otherwise expose the service through current settings flow.
- [ ] **FND-058 [TEST]** Verify a fake provider can create/confirm `TraceBackendRef` and provider failure never rolls back a committed Run.

**P0 exit gate**: Core imports and all Session/Run flows pass with no vendor SDK installed and no Sink configured.

## Phase 5 - Transactional Outbox (P0)

### Tests first

- [ ] **FND-060 [TEST]** Write failure-injection tests proving Session/Run/Artifact mutation rolls back if Outbox insert fails.
- [ ] **FND-061 [TEST]** Write event serializer tests proving payloads contain routing/summary fields only and reject sensitive keys.
- [ ] **FND-062 [TEST]** Write real-MySQL concurrency tests for two dispatcher workers using `SKIP LOCKED`.
- [ ] **FND-063 [TEST]** Write crash-window tests: dispatch succeeds, acknowledgement fails, retry is deduplicated by immutable event ID.
- [ ] **FND-064 [TEST]** Write retry/backoff/exhaustion/admin-retry tests.

### Implementation

- [ ] **FND-065 [DB]** Add `OutboxEvent` model/migration with unique event/idempotency keys, status, lease, attempts, next-attempt and last-error fields.
- [ ] **FND-066 [CORE]** Add domain event builders for `AgentSessionStarted`, `AgentRunRegistered`, `AgentRunCompleted`, `AgentRunTrustDegraded` and `TraceArtifactStored`.
- [ ] **FND-067 [CORE]** Insert events in the same SQLAlchemy Unit of Work as each domain mutation; remove any direct Celery enqueue from that transaction.
- [ ] **FND-068 [WORKER]** Implement bounded Outbox dispatcher with lease recovery, exponential backoff, idempotent handler contract and safe logs/metrics.
- [ ] **FND-069 [OPS]** Add pending-age/failed-count health reporting and an authenticated admin retry operation.

**Acceptance**: No committed domain transition can exist without its matching Outbox event; retries do not duplicate effects.

## Phase 6 - Release Seam, Compatibility and Rollout (P0)

- [ ] **FND-070 [TEST]** Keep the FND-001 WorkTrace/structured-report regression suite green after AgentRun introduction.
- [ ] **FND-071 [TEST]** Add a pending/failing contract test that defines future Release Gate evidence lookup by exact release candidate/deployment revision, not latest Namespace evaluation.
- [ ] **FND-072 [CORE]** Introduce a provider-neutral evidence lookup seam without routing current Release Gate decisions through new runtime evaluation data.
- [ ] **FND-073 [TEST]** Prove characterized Release Gate results remain unchanged in Foundation implementation.
- [ ] **FND-074 [TEST]** Run full unit/integration suite plus real-MySQL migration, locking and rollback lanes.
- [ ] **FND-075 [TEST]** Validate module import, compile, lint and type checks with optional observability dependencies absent.
- [ ] **FND-076 [OPS]** Validate default Docker Compose service set is unchanged and observability profile is not started implicitly.
- [ ] **FND-077 [OPS]** Exercise staging expand/backfill/contract, feature-flag enablement, dispatcher stop and application backout; retain evidence.
- [ ] **FND-078 [DOC]** Update operator/API documentation after implementation, including payload limits, error semantics, unresolved tenant remediation and rollback.

## Explicitly Deferred Tasks

- [ ] **NEXT-001** Build OTel Collector auth/mapping/redaction/normalization reference configuration.
- [ ] **NEXT-002** Implement OpenInference/OTel GenAI normalization and Langfuse adapter.
- [ ] **NEXT-003** Implement ATIF codec, MinIO content lifecycle, import/export and replay.
- [ ] **NEXT-004** Add Dataset, Evaluator, Evaluation, Experiment and DeepEval adapter.
- [ ] **NEXT-005** Bind immutable runtime evaluation evidence to Release Candidate and enforce it in Release Gate.
- [ ] **NEXT-006** Add evaluation/experiment UI and baseline comparison.

These items are not part of Foundation implementation acceptance and must not be pulled forward to compensate for a failing P0 foundation task.

## Foundation Phase Final Checklist

- [ ] All P0 tasks are complete; no skipped tenant/security/atomicity test.
- [ ] Expand/backfill/contract was validated against real MySQL with populated data.
- [ ] New writes always carry Namespace; cross-tenant references are rejected.
- [ ] Deployment revisions are immutable after activation.
- [ ] Run lifecycle is metadata-only, credential-derived and idempotent.
- [ ] Outbox is atomic and dispatcher is retry-safe.
- [ ] Core starts without Langfuse, DeepEval or ATIF packages.
- [ ] No raw trace, prompt or tool payload was added to MySQL.
- [ ] Existing WorkTrace and Release Gate behavior remains green.
- [ ] Rollout/backout evidence is attached to the implementation review.
