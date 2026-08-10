# Local integrated candidate rehearsal — 2026-08-07

## Scope and authority

This rehearsal validates the current DuckDock 2.0 release candidate on one
developer Mac after the old development data was cleared. It is local,
non-authorizing evidence. It does not replace target-environment TLS, network,
secret rotation, capacity, HA, recovery, independent security or human approval
evidence.

The repository source at the start of the rehearsal was commit `5bbfb7e8053c`
on `codex/release-2.0-rc1`. The working tree additionally contained the two
reliability fixes described below and was retested in full before commit.

## Integrated stack

- DuckDock backend, frontend, Celery worker, Beat, Analysis Worker, MySQL,
  Redis and MinIO were running.
- Langfuse Web/Worker 4.1.0, PostgreSQL, ClickHouse, the Langfuse OTLP
  collector and Prometheus were running.
- `/health`, `/readyz`, Langfuse health, Prometheus readiness, OTel health and
  the local Hermes OpenAPI document all returned success.
- A ten-minute post-fix scan found no error, exception, invalid-token, 5xx or
  traceback match in the DuckDock, Langfuse, OTel or Prometheus service logs.

## Provider compatibility

The Langfuse v4 compatibility gate passed all 60 checks against the live local
server. It covered authenticated server access, Clinic spans/generations,
Observations API v2 raw IO, Annotation Queue lifecycle, Scores API v3,
Dataset/Experiment flows and direct OTLP v4 ingestion. DuckDock remains bound
to the provider adapter rather than the Langfuse database schema.

The running local Hermes desktop exposed a live `Hermes Agent` 0.20.0 OpenAPI
service. A real Reporter execution completed the following path without reading
transcripts:

```text
Hermes public metadata
  -> Reporter enroll
  -> hermes-reporter v2 handshake + heartbeat
  -> metadata-only Session/Run
  -> structured report
  -> WorkTrace/assets/memory candidates
  -> Runtime Fleet
```

The resulting Runtime was DD-C1, ACTIVE and HEALTHY; its Run ended SUCCEEDED
with `CHANNEL_AUTHENTICATED/REPORTER` trust and `metadata_only` capture.
Environment-specific IDs and credentials are intentionally omitted.

This Hermes process is a headless desktop API, not an OpenAI-compatible model
endpoint. Its configured LM Studio endpoint was unavailable, and a temporary
PATH-corrected OpenAI Codex provider probe produced no response within 60
seconds. Therefore this rehearsal proves the Reporter/control-plane integration
but does **not** claim a fresh Hermes model-inference pass.

## Browser and automated gates

- Playwright: 3/3 passed for unauthenticated guard, login form and the full
  approve -> execute -> receipt -> verify -> completed handover loop.
- In-app browser: administrator login, Operations and Runtime Fleet loaded;
  the live Hermes Runtime was visible and healthy; console warnings/errors: 0.
- Backend: 1245 passed, 19 skipped.
- Frontend: 11 files / 56 tests; lint and production build passed; largest
  entry chunk 569.69 kB.
- GA/target-evidence and production-operations focused tests: 252 passed.
- Alembic: live database at `20260804_0062`; one head; autogenerate check clean.
- Frozen OpenAPI v2 digest:
  `aa260f301acc5c3a8004d14980952a03ce0197f9d70dcdd78cd62e986c3b1a83`.
- Production repository baseline: 35/35 passed.
- Python and frontend dependency audits: 0 known vulnerabilities at their
  configured thresholds.
- mypy ratchet: 81 errors, below the ceiling of 82.

## Defects found and fixed

1. Clearing the development database removed the Analysis Worker database row
   while `.env` retained its token. `scripts/dev.sh worker` then entered a 401
   restart loop. The dev command now performs a DEBUG-only, idempotent bootstrap
   that validates the configured token, refuses collisions/disabled workers and
   never prints the secret before starting the profile.
2. The handover E2E seed created a Namespace directly but did not preserve the
   product invariant that its owner is an ADMIN member. This made browser Fleet
   validation unable to select the seeded Namespace. The seed and regression
   test now enforce owner membership.

## Live readiness result after data cleanup

The current development database reports `BLOCKED`: 7 PASS, 0 WARN, 7 BLOCK.
Migration, frozen contract, Runtime inventory, reconciliation, Outbox,
operations incidents and Prometheus pass. Verified Package, applied release
receipt, signed Handover, directory offboarding, signing-key rotation, recovery
receipt and release SLO evidence are absent because the old test evidence was
intentionally cleared.

This is the correct fail-closed behavior. The earlier isolated candidate
rehearsal remains the reproducible application-level 14/14 result; neither one
authorizes production GA.

## Verdict

The candidate meets the repository and application-level bar for an internal
RC, integration acceptance and controlled pilot. It does not meet the DuckDock
2.0 GA publication bar. GA remains blocked on a final signed tag/provenance,
real target evidence, independent security assessment, sustained target load
and fault-domain validation, and four distinct human approvals.
