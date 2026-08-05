# G1 Evidence Alpha — Technical Acceptance Package

**Recorded**: 2026-07-28 (Asia/Shanghai)
**Repository**: `BaiKudan/DuckDock`
**Database revision**: `20260728_0034 (head)`
**Scope status**: Foundation EF-01～EF-10 PASS; G1 performance and
cross-channel reconciliation technical lanes PASS; Product/Architecture Owner
approved M1/G1 on 2026-07-30.

This record is the reproducible handoff evidence for FND-070～078 and the G1
technical gate. It does not claim that a local Compose exercise is a
production disaster-recovery test.

## 1. Compatibility and Release Evidence Seam

| Requirement | Result | Evidence |
|---|---|---|
| WorkTrace remains distinct from AgentRun | PASS | `test_work_trace_compatibility.py`; structured-report fields and dedupe remain green |
| Current Release Gate result is unchanged | PASS | `test_release_gate_compatibility.py`, `test_release_gate.py` |
| Future evidence is candidate-pinned | PASS | `test_release_evidence_seam.py`; selector requires Namespace, exact release-candidate ref, Deployment public ID and positive revision |
| No Namespace-latest fallback | PASS | Different candidate or Deployment revision returns no evidence |
| Provider neutral / optional SDK free | PASS | Null/InMemory port; blocked-SDK import test imports all core model/service packages |

The Foundation Release Gate still uses the characterized Clinic compatibility
lookup. `ReleaseGateService.evaluate()` does not call the new runtime evidence
port. A later Release Candidate specification must opt in explicitly.

## 2. Migration and Tenant Contract Evidence

Development Compose:

- `alembic current` → `20260728_0034 (head)`.
- `alembic check` → `No new upgrade operations detected`.
- Read-only tenant contract preflight → `safe=true`, `total_blockers=0`; all
  five null counters and all six relationship/duplicate counters are zero.

Isolated MySQL 8.4 lane:

- 16 MySQL-marked tests passed.
- Populated `0026 → head` upgrade preserves rows.
- Revision 0029 rejects unresolved ownership before DDL, then accepts explicit
  remediation and preserves the corrected rows.
- Concurrent Run lifecycle and Outbox `SKIP LOCKED` lanes pass.

## 3. Rollout and Backout Drill

Environment: local Docker Compose used as the staging-equivalent development
stack. The application source is bind-mounted, so application routing backout
was exercised through the supported ingestion switch rather than by pretending
to deploy a previous immutable image.

1. Recorded revision and row counts before the drill:
   `0033`, `agent_sessions=0`, `agent_runs=0`,
   `agent_run_artifacts=0`, `outbox_events=0`.
2. Stopped `beat` gracefully; container exited with status 0.
3. Recounted Outbox rows while the dispatcher schedule was stopped:
   `outbox_events=0`.
4. Restarted `beat`; the dispatcher schedule resumed.
5. Recreated backend with `AGENT_EXECUTION_INGESTION_ENABLED=true`;
   readiness passed.
6. Recreated backend with the switch set to `false`; the setting was confirmed
   false and MySQL/Redis/MinIO readiness stayed green.
7. Recounted all four protected tables; counts were unchanged.
8. Restored the switch to `true`; readiness passed and development ingestion
   was confirmed enabled.

The automated backout contract uses non-empty data: it starts a Session,
disables new starts, completes the already accepted Session, and proves both
the terminal domain row and its `AgentSessionStarted` /
`AgentSessionCompleted` Outbox events remain present. A Runtime allowlist test
also proves enabled and non-enabled Runtime behavior.

Routine backout does not downgrade schema, delete Outbox rows, null tenant
ownership, or rewrite terminal Run/Session facts.

## 4. Run-Control-Envelope Load Gate

The repeatable gate is
`backend/scripts/g1_run_control_load_gate.py`. It refuses the normal
application database, requires an isolated MySQL database whose name contains
`test` or `perf`, exercises the complete FastAPI ASGI HTTP/authentication/
validation/transaction path, and drops the isolated tables on exit.

Acceptance semantics:

- sustained: 100 requests/s for five seconds, zero errors, completion rate at
  least 95 requests/s, p95 at most one second and drain at most one second;
- short burst: 500 requests offered over one second, zero errors, scheduling
  lag at most 100 ms, p95 at most two seconds and drain at most two seconds;
- every accepted request must produce exactly one AgentRun, one
  `agent_run.started` audit row and one `AgentRunRegistered` Outbox event;
- same key/same body must return 201 without another row; same key/different
  body must return 409.

Three consecutive isolated MySQL 8.4 runs passed:

| Run | Sustained 100/s | Sustained p95 | Burst offered | Burst schedule lag | Burst p95 | Burst drain | Materialization |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 500/500 | 16.052 ms | 500/500 in 1 s | 4.583 ms | 788.860 ms | 0.460 s | 1000 Run/Audit/Outbox |
| 2 | 500/500 | 17.166 ms | 500/500 in 1 s | 2.673 ms | 886.816 ms | 0.493 s | 1000 Run/Audit/Outbox |
| 3 | 500/500 | 17.322 ms | 500/500 in 1 s | 18.669 ms | 601.861 ms | 0.484 s | 1000 Run/Audit/Outbox |

The gate deliberately measures application ingress with real MySQL but without
external TLS/load-balancer network latency. Production edge and long-duration
capacity tests remain G6 work.

The hot path keeps legacy PBKDF2 Reporter hashes readable but stores new
high-entropy machine-token secrets as versioned, server-keyed HMAC-SHA256.
Execution authentication uses one joined tenant/runtime lookup, does not
synchronously contend on `last_used_at`, and uses an atomic MySQL Run identity
claim so unrelated idempotency keys remain concurrent.

## 5. v1 Reporter to v2 Reconciliation

Revision 0034 adds idempotent consumer receipts and a safe reconciliation
projection. A Structured Report writes a low-sensitive
`StructuredReportRecorded` Outbox event in the same transaction as its
WorkTrace. It does **not** manufacture an AgentRun from a daily/weekly
management summary.

Reconciliation is based only on the stored allowlisted event and typed
`AgentRun.work_trace_id` links:

| Status | Meaning |
|---|---|
| `MATCHED` | One or more typed Run links agree on Namespace and Runtime. |
| `EXPECTED_LEGACY_ONLY` | The v1 management summary has no execution fact; this is explained, not a mismatch. |
| `MISMATCH` | Event, WorkTrace or linked Run tenant/runtime scope disagrees; this is an unexplained difference. |

Evidence:

- SQLite tests cover safe payloads, no synthetic Run, atomic rollback, matched,
  expected-legacy-only, cross-tenant mismatch and consumer replay;
- the real-MySQL test proves one projection and one receipt after duplicate
  consumption;
- system-admin health/get/recompute APIs expose counts and reason codes, never
  report content or the internal Outbox event ID;
- a live Compose smoke created one temporary Structured Report, observed the
  Beat/Worker event become `PUBLISHED`, received one
  `EXPECTED_LEGACY_ONLY` projection and one receipt, proved replay was
  deduplicated, and then removed every temporary row by exact ID;
- the live smoke and isolated acceptance lane both ended with zero unexplained
  differences.

## 6. Default Runtime and Quality Gates

Default Compose services:

```text
mysql
redis
minio
backend
worker
beat
frontend
```

`postgres`, `clickhouse`, all `langfuse-*` services and `analysis-worker` are
absent unless their profiles are selected. The running service set matches the
default set and contains zero observability-only services.

| Gate | Result |
|---|---|
| Backend full suite | 829 passed, 16 skipped |
| Foundation suite | 184 passed, 12 skipped |
| Real MySQL marker lane | 16 passed |
| Core coverage gate | 80.58% ≥ 65% |
| Ruff / compileall / FastAPI import | PASS |
| mypy ratchet | 82 ≤ 82 |
| Frontend Vitest | 34 passed |
| Frontend lint / build | 0 errors, 9 existing warnings / PASS |
| Static + live v2 OpenAPI | PASS; 14 mounted v2 paths including reconciliation operations |
| Development readiness | backend/frontend ready; MySQL, Redis and MinIO green; Worker/Beat running |

## 7. Gate Boundary

This evidence closes the DuckDock 2.0 Foundation implementation phase and G1.
The Product/Architecture Owner recorded formal approval on 2026-07-30 after
reviewing the technical performance/reconciliation package. S3 may proceed;
this approval does not broaden G1 into production disaster-recovery evidence.

Production backup/restore and object-store recovery remain G6 work and are not
represented by this application backout drill.
