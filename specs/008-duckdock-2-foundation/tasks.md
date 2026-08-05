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
- [x] **FND-006 [DOC]** Record the ten accepted foundation decisions from `plan.md` in the implementation PR description and link each decision to its enforcing test. Evidence: GitHub PR #6 contains the ten-row FND-ADR-001 through FND-ADR-010 decision/test matrix and explicitly marks enforcement deferred beyond this batch.

**Exit gate**: FND-001 through FND-005 pass on the unchanged behavior baseline.

## Phase 1 - Direct Namespace Ownership (P0)

### Tests first

- [x] **FND-010 [TEST]** Create real-MySQL fixtures for: empty DB, fully deterministic single-tenant legacy data, unresolved data, and a Runtime bound to Assets from two Namespaces. Evidence: `test_tenant_expand_migration.py`, `test_tenant_backfill_mysql.py` and `test_tenant_contract_migration.py`; full MySQL marker lane 13 passed, including a REPEATABLE READ CAS race and unsafe/safe contract migration.
- [x] **FND-011 [TEST]** Write failing tenant resolver tests for precedence, repeatability, unresolved classification and conflict classification. Evidence: `test_tenant_resolution_service.py`, 17 passed after real red runs for the missing module, soft-deleted historical identity, WorkTrace fallback safety and complete nested-conflict candidates.
- [x] **FND-012 [TEST]** Write failing API/service tests proving new Runtime, Asset, Binding, WorkTrace and Evidence records cannot be created without an authorized Namespace. Evidence: `test_tenant_write_enforcement.py`; management schemas require Namespace, API authorization is enforced, and the trusted ingestion/materialization builders fail closed.
- [x] **FND-013 [TEST]** Write failing tests that reject Binding/WorkTrace/Evidence relationships across Namespaces. Evidence: `test_tenant_write_enforcement.py`, handover tenant regressions, and the centralized same-tenant builders.
- [x] **FND-014 [TEST]** Write a migration test proving contract migration aborts with actionable counts when unresolved/conflict rows remain. Evidence: `test_tenant_contract_migration.py` 在真实 MySQL 构造五类 NULL blocker，断言 revision 0029 在任何 DDL 前以确定性 JSON 和处置命令拒绝；显式修复后可升级并保留数据。

### Implementation

- [x] **FND-015 [DB]** Add expand migration with nullable, indexed `namespace_id` FKs on `RuntimeInstance`, `AIAsset`, `RuntimeBinding`, `WorkTrace` and `EvidenceItem`. Evidence: revision `20260717_0027`, 8 migration tests, real MySQL populated upgrade, `upgrade head` and `alembic check`.
- [x] **FND-016 [CORE]** Implement deterministic `TenantResolutionService`; return resolution rule and candidates, never infer from Membership, names or free-form metadata. Evidence: `app/services/tenant_resolution_service.py`; 17 resolver tests and five real-MySQL S1-B fixtures.
- [x] **FND-017 [OPS]** Implement an idempotent batch backfill/audit command with JSON and console reports, checkpointing and non-zero exit on unresolved/conflict. Evidence: `app/services/tenant_backfill_service.py`, `scripts/backfill_foundation_tenants.py`, 14 service/CLI tests, mode/database/revision-bound atomic checkpoints, CAS current-read conflict handling and `tenant-backfill-runbook.md`.
- [x] **FND-018 [CORE]** Update all new-write services and tenant-scoped repositories to require Namespace and validate same-tenant relationships. Evidence: `tenant_write_service.py`; Runtime/Reporter/Asset/Handover management writes plus adapter, structured-report, analysis-materializer and handover-package write paths; frontend and Hermes pilot require an explicit Namespace.
- [x] **FND-019 [DB]** Add contract migration preflight, non-null constraints and tenant-scoped indexes. Document the exact remediation/retry path in migration output. Evidence: revision `20260728_0029` 复用完整 blocker 定义；五个 `namespace_id` 已设为 non-null，四个 tenant-scoped composite indexes 与 Binding tenant-key unique constraint 已落地；开发库保留零 blocker 报告后成功升级。

**Acceptance**:

- [x] Backfill run twice produces the same state and no duplicate audit effects.
- [x] Real MySQL rejects unsafe contract migration and accepts it after explicit remediation.
- [x] No service request can create or link a cross-tenant record.

## Phase 2 - Immutable Deployment Inventory (P0)

### Tests first

- [x] **FND-020 [TEST]** Write model/service tests for `REGISTERED -> ACTIVE -> RETIRED` and `REGISTERED -> FAILED` transitions. Evidence: `test_deployment_inventory.py`.
- [x] **FND-021 [TEST]** Write failing tests proving ACTIVE/RETIRED Deployment identity, revision, digest, Runtime and components are immutable. Evidence: registered-only mutation services reject ACTIVE/RETIRED snapshots.
- [x] **FND-022 [TEST]** Write failing tests for cross-tenant Runtime/Asset/SkillVersion component references and missing version/digest identity. Evidence: four cross-tenant cases plus strict Pydantic component identity/config tests.
- [x] **FND-023 [TEST]** Write concurrent registration tests for duplicate `(namespace, runtime, external_deployment_id, revision)`. Evidence: real MySQL two-session race produces one winner and one deterministic duplicate.

### Implementation

- [x] **FND-024 [DB]** Add `AgentDeployment` and `DeploymentComponent` models/migration with constraints and indexes from `data-model.md`. Evidence: revision `20260728_0030`; empty MySQL upgrade/check and development upgrade/check clean.
- [x] **FND-025 [CORE]** Implement register, activate and retire services with immutable snapshot semantics and audit events. Evidence: `deployment_service.py`; row locks, revalidation and transactional audit actions.
- [x] **FND-026 [API]** Add Namespace/RBAC-scoped register, activate, retire, list and detail endpoints using existing API conventions. Evidence: `/api/v1/deployments` routes and non-enumerable unauthorized detail.
- [x] **FND-027 [API]** Add strict Pydantic schemas and OpenAPI examples; never accept secret or raw prompt content in component configuration. Evidence: allowlisted `DeploymentComponentConfiguration`, `extra=forbid`, digest/key patterns and OpenAPI smoke.
- [x] **FND-028 [TEST]** Add end-to-end test: register revision 1, activate, reject mutation, register revision 2, retire revision 1. Evidence: `test_revision_one_lifecycle_then_revision_two_registration`.

**Acceptance**: [x] Every activated deployment is an immutable, tenant-safe version snapshot.

## Phase 3 - Session/Run Metadata Ingestion (P0)

### Tests first

- [x] **FND-030 [TEST]** Write canonical JSON tests covering key order, optional/null fields, timestamp normalization, Unicode and numeric representation. Evidence: `test_run_canonicalization.py`; canonical JSON v1 normalizes UTC, NFC, null omission and integral floats before SHA-256.
- [x] **FND-031 [TEST]** Write start-session tests proving Runtime/Namespace are derived from `ReporterCredential` and forged request claims are rejected/audited. Evidence: `test_execution_api_v2.py`; HTTP start response uses credential-derived IDs, governance fields return 422, and sanitized denial audit persists after rollback.
- [x] **FND-032 [TEST]** Write start-run tests for same-key/same-hash replay, same-key/different-hash 409, and concurrent duplicate requests. Evidence: unit replay/conflict plus real MySQL two-session start race.
- [x] **FND-033 [TEST]** Write complete-run tests for legal transitions, identical replay, conflicting terminal state and completion races. Evidence: all four terminal states plus real MySQL conditional completion race.
- [x] **FND-034 [TEST]** Write strict-schema tests rejecting `prompt`, `messages`, `completion`, `tool_arguments`, `tool_result` and oversized/deep metadata. Evidence: strict Pydantic envelopes and allowlisted 16 KiB metadata.
- [x] **FND-035 [TEST]** Write trust tests proving bearer authentication yields `CHANNEL_AUTHENTICATED + REPORTER`, while only a successful verifier can yield `PRODUCER_ATTESTED`. Evidence: bearer/failed/successful verifier contract tests plus rejected and audited client trust escalation.
- [x] **FND-036 [TEST]** Write management read tests for tenant isolation, pagination/time bounds and non-enumerable foreign IDs. Evidence: required timezone-aware 31-day window, signed filter-bound cursor, Namespace RBAC and indistinguishable foreign/unknown 404 tests.

### Implementation

- [x] **FND-037 [DB]** Add `AgentSession` and `AgentRun` models/migration with public IDs, state constraints, tenant-scoped uniqueness, hashes and aggregate fields. Evidence: revision `20260728_0031`; empty/development MySQL upgrade and check clean.
- [x] **FND-038 [CORE]** Implement validated-envelope canonicalization and SHA-256 utility; hash only after strict schema validation. Evidence: `envelope_canonicalization_service.py`, `CANONICALIZER_VERSION=duckdock-canonical-json-v1`.
- [x] **FND-039 [CORE]** Implement Session/Run state services and conditional terminal transition. Evidence: `execution_service.py`; atomic MySQL Run identity claim without unrelated per-Runtime serialization, row-locked completion, same-hash replay and tenant/reference validation.
- [x] **FND-040 [SEC]** Extend Reporter scope enforcement for Run ingestion and centralize credential -> Runtime -> Namespace resolution. Evidence: `reporter_identity_service.py`; v2 requires `ReporterCredential + execution.write`, rejects legacy tokens/disabled Runtime, derives active Namespace/Runtime/actor/trust in one joined lookup, and stores new high-entropy machine tokens as versioned server-keyed HMAC-SHA256 while retaining legacy PBKDF2 verification.
- [x] **FND-041 [API]** Implement Session start/complete and Run start/complete endpoints listed in `spec.md`. Evidence: mounted `/api/v2/reporter/*` router, required bounded idempotency header, strict metadata-only validation and credential-scoped completion.
- [x] **FND-042 [API]** Implement tenant-scoped AgentRun list/detail returning metadata, version references and trace links only. Evidence: `/api/v2/agent-runs`; 31-day bounded queries, status/runtime/deployment filters, signed cursor and metadata-only projection without internal hashes/keys.
- [x] **FND-043 [AUDIT]** Audit forged identity claims, idempotency conflicts, invalid state transitions and trust degradation without logging tokens or content. Evidence: rejection transaction rollback followed by sanitized audit-only commit; field names/reason codes only, with token/body exclusion assertions.

**Acceptance**:

- [x] Same request can be retried safely after timeout.
- [x] Different content cannot hide behind an existing idempotency key.
- [x] Raw execution content never reaches the persistence service.

## Phase 4 - Artifacts and Provider Ports (P0/P1)

### P0 data and boundaries

- [x] **FND-050 [TEST]** Write failing model tests for artifact checksum/size/schema, internal object key validation and same-tenant Run relation. Evidence: `test_agent_artifact_telemetry.py`; external/file/traversal URI, checksum, size, idempotency and cross-tenant cases.
- [x] **FND-051 [TEST]** Write Port contract tests for null/in-memory TelemetrySink, TrajectoryCodec and AttestationVerifier adapters. Evidence: deterministic null/in-memory adapter round trips with no vendor imports.
- [x] **FND-052 [DB]** Add `AgentRunArtifact`, `TelemetrySink` and `TraceBackendRef` models/migration with constraints from `data-model.md`. Evidence: revision `20260728_0032`; development MySQL upgrade and `alembic check` clean.
- [x] **FND-053 [CORE]** Define inward-facing `TelemetrySinkPort`, `TrajectoryCodecPort` and `AttestationVerifierPort`; type them without vendor classes. Evidence: `app/services/telemetry_ports.py`.
- [x] **FND-054 [CORE]** Implement null adapters so the core works with no optional provider packages or configuration. Evidence: core import and Foundation suite pass without a configured Sink.
- [x] **FND-055 [SEC]** Validate Telemetry Sink endpoint/config and persist `credential_ref` only; add a regression test that secret-looking fields are rejected. Evidence: strict schemas, write-time SSRF guard, credential ownership check and secret-field regressions.
- [x] **FND-056 [CORE]** Add AgentRunArtifact metadata registration service; defer object parsing and ATIF validation. Evidence: metadata-only registration/list service; no storage read or codec invocation.

### P1 optional management surface

- [x] **FND-057 [API]** Add RBAC-scoped TelemetrySink create/list/update/disable endpoints if required by the existing admin UX; otherwise expose the service through current settings flow. Evidence: `/api/v2/telemetry-sinks*` and `/api/v2/agent-runs/{run_public_id}/artifacts`, Namespace member/writer enforcement and cross-tenant 404.
- [x] **FND-058 [TEST]** Verify a fake provider can create/confirm `TraceBackendRef` and provider failure never rolls back a committed Run. Evidence: success/error fake-port tests; provider exception bodies are discarded and committed Run remains readable.

**P0 exit gate**: Core imports and all Session/Run flows pass with no vendor SDK installed and no Sink configured.

## Phase 5 - Transactional Outbox (P0)

### Tests first

- [x] **FND-060 [TEST]** Write failure-injection tests proving Session/Run/Artifact mutation rolls back if Outbox insert fails. Evidence: start and completion transition injection tests cover Session, Run and Artifact rollback.
- [x] **FND-061 [TEST]** Write event serializer tests proving payloads contain routing/summary fields only and reject sensitive keys. Evidence: exact per-event allowlists, scalar/16 KiB bounds, required fields and secret-alias regressions.
- [x] **FND-062 [TEST]** Write real-MySQL concurrency tests for two dispatcher workers using `SKIP LOCKED`. Evidence: two MySQL workers claim disjoint five-event batches.
- [x] **FND-063 [TEST]** Write crash-window tests: dispatch succeeds, acknowledgement fails, retry is deduplicated by immutable event ID. Evidence: expired lease redelivery uses the same event ID and produces one fake-consumer effect.
- [x] **FND-064 [TEST]** Write retry/backoff/exhaustion/admin-retry tests. Evidence: exponential 5/10-second backoff, FAILED exhaustion, sanitized errors, health counts and admin reset.

### Implementation

- [x] **FND-065 [DB]** Add `OutboxEvent` model/migration with unique event/idempotency keys, status, lease, attempts, next-attempt and last-error fields. Evidence: revision `20260728_0033`; development and populated MySQL upgrade/check clean.
- [x] **FND-066 [CORE]** Add domain event builders for `AgentSessionStarted`, `AgentRunRegistered`, `AgentRunCompleted`, `AgentRunTrustDegraded` and `TraceArtifactStored`. Evidence: provider-neutral builders plus `AgentSessionCompleted` for every Session transition.
- [x] **FND-067 [CORE]** Insert events in the same SQLAlchemy Unit of Work as each domain mutation; remove any direct Celery enqueue from that transaction. Evidence: lifecycle and artifact services enqueue only through their active AsyncSession; request transactions contain no Celery import/call.
- [x] **FND-068 [WORKER]** Implement bounded Outbox dispatcher with lease recovery, exponential backoff, idempotent handler contract and safe logs/metrics. Evidence: Beat-driven dispatcher, stable event ID, `SKIP LOCKED`, expired-lease recovery and payload-free logs.
- [x] **FND-069 [OPS]** Add pending-age/failed-count health reporting and an authenticated admin retry operation. Evidence: `/api/v2/outbox/health` and `/api/v2/outbox/{event_id}/retry`, system-admin dependency and payload-free responses.

**Acceptance**: No committed domain transition can exist without its matching Outbox event; retries do not duplicate effects.

## Phase 6 - Release Seam, Compatibility and Rollout (P0)

- [x] **FND-070 [TEST]** Keep the FND-001 WorkTrace/structured-report regression suite green after AgentRun introduction. Evidence: WorkTrace compatibility and full backend suites remain green; AgentRun never replaces structured-report materialization.
- [x] **FND-071 [TEST]** Add a pending/failing contract test that defines future Release Gate evidence lookup by exact release candidate/deployment revision, not latest Namespace evaluation. Evidence: `test_release_evidence_seam.py` first failed on the missing port, then locked Namespace + candidate ref + Deployment public ID/revision and rejected latest aliases.
- [x] **FND-072 [CORE]** Introduce a provider-neutral evidence lookup seam without routing current Release Gate decisions through new runtime evaluation data. Evidence: `release_evidence_ports.py` Null/InMemory adapters and `ReleaseGateService.lookup_candidate_evidence()`; current `evaluate()` does not call the port.
- [x] **FND-073 [TEST]** Prove characterized Release Gate results remain unchanged in Foundation implementation. Evidence: pass/below-threshold/missing Clinic fixtures and legacy Gate suite remain green; a fail-if-called future port proves isolation.
- [x] **FND-074 [TEST]** Run full unit/integration suite plus real-MySQL migration, locking and rollback lanes. Evidence: backend 829 passed/16 skipped; Foundation 184 passed/12 skipped; isolated MySQL 16 passed; populated migration, contract rejection/remediation, concurrent Run lifecycle, Outbox `SKIP LOCKED` and reconciliation replay pass.
- [x] **FND-075 [TEST]** Validate module import, compile, lint and type checks with optional observability dependencies absent. Evidence: vendor-SDK-blocking import test, Ruff/compile/FastAPI import clean and mypy `82≤82`.
- [x] **FND-076 [OPS]** Validate default Docker Compose service set is unchanged and observability profile is not started implicitly. Evidence: default/running set is MySQL, Redis, MinIO, backend, worker, beat and frontend; zero Postgres/ClickHouse/Langfuse services.
- [x] **FND-077 [OPS]** Exercise staging expand/backfill/contract, feature-flag enablement, dispatcher stop and application backout; retain evidence. Evidence: safe zero-blocker preflight, ingestion switch/Runtime allowlist test, graceful Beat stop/restart, live true→false→true backend drill and invariant counts in `evidence/g1-evidence-alpha-20260728.md`.
- [x] **FND-078 [DOC]** Update operator/API documentation after implementation, including payload limits, error semantics, unresolved tenant remediation and rollback. Evidence: OpenAPI start-route 503/Retry-After and reconciliation contracts, Quickstart rollout/backout/load-gate instructions and G1 technical acceptance record.

## G1 Technical Acceptance

- [x] **G1-001 [PERF]** Sustain 100 Run-control envelopes/s for five seconds and offer a 500-request one-second burst through FastAPI authentication/validation with real MySQL. Evidence: three consecutive passing runs in `evidence/g1-evidence-alpha-20260728.md`; zero errors and exact Run/Audit/Outbox materialization.
- [x] **G1-002 [COMPAT]** Reconcile v1 Structured Report events with typed v2 AgentRun links without manufacturing execution facts. Evidence: revision `20260728_0034`, safe Outbox consumer receipts/projections, replay tests, real-MySQL lane and zero unexplained differences.
- [x] **G1-003 [OPS]** Verify the reconciliation consumer in the live default Compose stack and remove temporary test rows by exact ID. Evidence: one event reached `PUBLISHED`, one receipt/projection was materialized, replay deduplicated and cleanup returned the stack to zero temporary rows.
- [x] **G1-004 [GOV]** Record formal M1/G1 approval by the Product/Architecture Owner. Evidence: Owner confirmation “确认，继续。” recorded on 2026-07-30 after review of the complete technical acceptance package.

## Next-Phase Tasks

- [x] **NEXT-001** Build OTel Collector auth/mapping/redaction/normalization reference configuration. Evidence: pinned Collector 0.157.0 edge profile, Basic Auth on OTLP HTTP/gRPC, server-injected Runtime/Namespace/trust, fail-closed metadata allowlist, Secret Canary fixture, executable smoke and `evidence/s3-next001-openclaw-shadow-20260730.md`.
- [x] **NEXT-002** Implement OpenInference/OTel GenAI normalization and Langfuse adapter. Evidence: explicit Runtime MCP Session/Run lifecycle correlation; pinned Collector `gen_ai_normalizer` with OpenInference/direct OTel GenAI golden equivalence; fail-closed content removal; optional Langfuse v4 OTLP overlay; bounded Observations API v2 confirmation adapter; live protocol mock and `evidence/s3-next002-genai-langfuse-20260730.md`.
- [x] **RT-05** Implement OpenClaw durable control buffering and Collector WAL replay. Evidence: Runtime MCP v0.3.0 metadata-only SQLite WAL, ordered dependency resolution, durable ack cursor, bounded pressure and explicit `PARTIAL` / `DECLARED_LOSS` markers; Collector `file_storage` WAL; forced-KILL replay without a second producer request; `evidence/s3-rt05-durable-replay-20260730.md`.
- [x] **RT-06** Implement Generic OTLP trusted bridge, mapping-conflict quarantine and DD-C2 conformance. Evidence: per-Runtime authenticated Collector, metadata-only durable dual export, ReporterCredential-derived projection identity, revision 0035 `GenericTraceProjection`, canonical Run/Trace mapping, duplicate/late isolation, provider-outage replay without a second producer request, OTL-001～009/CONF-C2-001～009 and `evidence/s4-rt06-generic-otlp-20260730.md`.
- [x] **RT-07** Implement authenticated Pack/ATIF manifest preflight, existing-Run import, content-addressed MinIO integrity and idempotent replay. Evidence: revision 0036 `PackImport`/`PackImportArtifact`; strict `duckdock-pack/1.0`; ATIF v1.0–v1.7 recognition; whole-Pack/per-payload checksum; traversal/duplicate/symlink/encryption/bomb defense; fail-closed content/Secret Canary; PAT-001～012; live MinIO signed PUT/finalize/readback; `evidence/s4-rt07-pack-atif-20260730.md`.
- [x] **RT-08** Implement shared DD-C0 negotiation, dynamic capability snapshots, heartbeat/config/Collector drift and the Runtime Fleet read/UI model. Evidence: revision 0037; CONF-C0-001～007; three profile-specific claims; real MySQL history projection; `evidence/s4-rt08-dynamic-fleet-g2-20260730.md` and checksummed conformance report; Product/Architecture Owner explicitly approved G2 on 2026-07-31 with “批准，继续继续”.
- [x] **NEXT-003** Complete ATIF export, resumable multipart upload, durable batch ack/cursor and evaluation replay. Evidence: revision 0038; immutable `PackExport`; MinIO list-parts/ETag/size/whole-Pack verification; credential-scoped durable batch receipts and contiguous cursor; size/SHA-verified `EvaluationResultReplayed` Outbox event; real MySQL downgrade/upgrade/check; live MinIO multipart; `evidence/s4-next003-pack-ddc3-20260730.md`. Per-producer DD-C3 deployment certification still requires its client-side persistence/pressure/loss lanes.
- [x] **NEXT-004** Add Dataset, Evaluator, Evaluation, Experiment and DeepEval adapter. Evidence: revision 0040; immutable provider-hosted Dataset/Evaluator versions; target-pinned Experiment/Evaluation summaries; Langfuse v4 Dataset sync; optional DeepEval adapter outside core; real MySQL downgrade/upgrade/check; `specs/009-evaluation-hub/evidence/eval-hub-langfuse-v4-20260731.md`.
- [x] **EH-02** Execute Langfuse-backed Evaluations with a recoverable Worker. Evidence: revision 0041; transactional queue event; renewable lease/heartbeat/reclaim; idempotent duplicate dispatch; bounded retry and cancellation; pinned Langfuse v4 Experiment runner; real mixed-result run; isolated MySQL round trip; `specs/009-evaluation-hub/evidence/eval-hub-eh02-execution-20260731.md`.
- [x] **EH-03** Govern provider-hosted Evaluation result manifests and explicit partial results. Evidence: revision 0042; immutable manifest versions and safe API/Outbox projection; expected/processed/scored/pass/fail/error accounting; `COMPLETE/PARTIAL` state contract; partial retry retention; real Langfuse Public API digest reconstruction; isolated MySQL dirty-row round trip; `specs/009-evaluation-hub/evidence/eval-hub-eh03-result-manifest-20260731.md`.
- [x] **EH-04** Add exact baseline comparison, immutable regression policies and reproducibility views. Evidence: revision 0043; explicit baseline/candidate manifest and policy-version pins; same DatasetVersion/EvaluatorVersion comparability; PASS/REGRESSION/INCONCLUSIVE; no-latest regression lock; real Langfuse matched-item comparison and independently reconstructed digest; guarded MySQL round trip; `specs/009-evaluation-hub/evidence/eval-hub-eh04-comparison-20260731.md`.
- [x] **NEXT-005** Bind immutable runtime evaluation evidence to Release Candidate and enforce it in Release Gate. Evidence: revision 0044; immutable exact candidate/Deployment revision/Comparison binding with candidate target/config-digest verification; bound REGISTERED Deployment freeze; PASS-only exact-selector Gate with missing/regression/inconclusive fail-closed outcomes; safe Outbox/audit; guarded real-MySQL round trip; real Langfuse comparison binding and independently reconstructed Gate digest; `specs/009-evaluation-hub/evidence/eval-hub-eh05-release-gate-20260731.md`.
- [x] **NEXT-006** Add the Evaluation Hub UI on top of governed comparisons. Evidence: revision 0045 immutable human review; Eval Hub overview/comparison/release-gate UI; APPROVED/REJECTED final decision semantics; exact candidate Gate re-evaluation; real MySQL guarded round trip; real Langfuse-backed binding review; browser verification; `specs/009-evaluation-hub/evidence/eval-hub-eh06-ui-review-g3-20260731.md`; Product/Architecture Owner explicitly approved G3 on 2026-07-31 with “批准，继续继续”.
- [x] **NEXT-007** Add a Langfuse-native Trace2Dataset data-flywheel slice without coupling provider content to DuckDock storage. Evidence: revision 0046 immutable `EvaluationDatasetMaterialization`; deterministic provider item upsert; Observation v2 raw-IO adapter boundary; pinned DatasetVersion manifest; same-transaction audit/Outbox; API and Eval Hub UI; isolated MySQL round trip/guard; real Langfuse item/source/pin verification; compatibility gate and `specs/009-evaluation-hub/evidence/eval-hub-trace2dataset-20260731.md`.

These items are not part of Foundation implementation acceptance. They may
proceed only after G1 approval and must not be used to compensate for a failing
P0 foundation task.

## Foundation Phase Final Checklist

- [x] All P0 tasks are complete; no skipped tenant/security/atomicity test.
- [x] Expand/backfill/contract was validated against real MySQL with populated data.
- [x] New writes always carry Namespace; cross-tenant references are rejected.
- [x] Deployment revisions are immutable after activation.
- [x] Run lifecycle is metadata-only, credential-derived and idempotent.
- [x] Outbox is atomic and dispatcher is retry-safe.
- [x] Core starts without Langfuse, DeepEval or ATIF packages.
- [x] No raw trace, prompt or tool payload was added to MySQL.
- [x] Existing WorkTrace and Release Gate behavior remains green.
- [x] Rollout/backout evidence is attached to the implementation review.
