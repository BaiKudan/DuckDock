# NEXT-010 — decoupled Langfuse Annotation Queue evidence

Date: 2026-08-03
Result: PASS

## Accepted boundary

- DuckDock discovers and binds an existing Langfuse Annotation Queue. Queue
  creation remains a provider/operator concern.
- The binding freezes the exact Queue ref, name, Score Config IDs and provider
  update timestamp. Dispatch fails closed when the Score Config snapshot
  changes.
- A DuckDock transaction persists one immutable dispatch intent and item list
  before any provider call. Beat/Worker synchronization is lease-protected,
  retryable and independent from production sampling.
- The adapter lists and reconciles existing Observation items before create.
  This closes the provider-success/local-ack crash window without relying on
  Langfuse uniqueness, because Langfuse v4 permits duplicate queue items.
- MySQL/API/Audit/Outbox store only queue, Observation and provider item refs,
  states, counts and digests. Annotation values, corrections and score detail
  remain in Langfuse.
- Langfuse `COMPLETED` is progress only. It does not create a DuckDock curation
  approval or materialization.

## Real provider and browser acceptance

- Langfuse Web/Worker: `4.1.0`; Python SDK: `4.14.2`.
- Existing Queue: `cmscmcnwj0012oz07u71pvncn` /
  `duckdock.annotation.e2e.20260803`; one Score Config.
- Existing reproducible sampling batch:
  `ecb_1aaa4fc5a3344e6497ed10e18cb17c61`, three Hermes Observations.
- Local `/eval-hub` browser flow bound the Queue and created dispatch
  `ead_413908ffe3a44bad845374f245225d4b`.
- Recovery Beat/Worker advanced the dispatch from `PENDING` to `SYNCED` with
  `3/3` unique Observation refs and `3/3` unique provider item refs in one
  attempt.
- Repeating the same browser dispatch returned the same durable dispatch; no
  second MySQL dispatch or Langfuse item was created.
- The three real Langfuse items were changed from `PENDING` to `COMPLETED` via
  the public SDK. Browser reconciliation showed `3/3 completed`.
- The source curation batch still had zero reviews and zero materializations,
  and remained `PENDING_REVIEW` in the UI.
- `EvaluationAnnotationDispatchRequested` and
  `EvaluationAnnotationDispatchSynchronized` were both `PUBLISHED`. Their
  payloads and all dispatch audit rows contained no input, output, annotation
  value, correction, score detail or item array.
- The temporary acceptance user was logged out, removed from the Namespace
  and disabled; immutable creator/audit provenance remains.

## Upgrade and migration gates

- The real compatibility gate passed health/version, Clinic/Observations v2,
  Annotation Queue idempotent dispatch/completion reconciliation, Dataset
  source/pin, Experiment scores/manifest and direct OTLP v4 ingestion.
- The gate uses fixed Queue/Score Config fixtures and deletes its exact test
  queue item after each run.
- Isolated MySQL passed `0048 → 0049`, `alembic check`, empty downgrade to
  `0048`, re-upgrade to `0049`, and populated downgrade refusal.
- The development database is `20260803_0049 (head)` and `alembic check`
  reports no new operations.

## Automated regression

- Backend full suite: `951 passed, 19 skipped`.
- Frontend: `43 passed`; ESLint `0 errors` with 9 accepted existing warnings;
  production build PASS.
- Ruff: PASS.
- mypy: `82` existing errors in `24` files, unchanged from the accepted
  baseline; NEXT-010 adds none.

## Upgrade safety conclusion

Langfuse remains a replaceable provider behind an explicit v4 compatibility
profile. Existing DuckDock governance records do not require migration when
Langfuse upgrades. A new provider version is accepted only after the
Annotation Queue lane proves Queue/Score Config shape, reconcile-before-create
idempotency and completion-state reconciliation against the candidate.
