# NEXT-011 — annotation-driven Promotion recommendation evidence

Date: 2026-08-03
Result: PASS

## Accepted boundary

- DuckDock owns versioned Promotion governance; Langfuse remains the
  replaceable provider for Annotation Queue state, Score Configs, human score
  values, comments and corrections.
- Every immutable policy version pins one exact DuckDock Queue binding, the
  provider Queue ref, one Score Config ID/data type, a typed quality rule and
  an optional metadata-only diversity rule.
- A run requires one exact dispatch at `SYNCED`, `completed_count=item_count`
  and no failed item. Queue/Score Config drift, incomplete annotation, missing
  scores or missing metadata fail closed.
- The Langfuse adapter reads Scores API v3 with exact
  `source=ANNOTATION + queueId + configId + traceId + observationId` filters.
  Numeric/boolean/categorical values are evaluated ephemerally and converted
  to booleans plus SHA-256 evidence digests.
- MySQL/API/Audit/Outbox never persist raw score values, annotation comments
  or corrections. `RECOMMENDED` and `BLOCKED` are advisory outcomes only;
  neither creates a curation review or Dataset materialization.

## Real Hermes, Langfuse and browser acceptance

- Langfuse Web/Worker: `4.1.0`; Python SDK: `4.14.2`.
- Numeric Score Config:
  `a1662d66-6e48-49af-83c1-b286e0a76963` /
  `duckdock_promotion_quality`.
- Annotation Queue: `cmscpapod0024oz07i016rxte` /
  `duckdock.promotion.e2e.20260803`.
- DuckDock binding: `eaq_e66823426eea4dc6871748bc6c5ca33a`;
  dispatch: `ead_0b6c1c1710d9420c85744dcaaf529eab`.
- Existing curation batch:
  `ecb_1aaa4fc5a3344e6497ed10e18cb17c61`, containing three real Hermes
  Observations. All three provider queue items reconciled to `COMPLETED` and
  carried `ANNOTATION`-source numeric scores `0.95 / 0.90 / 0.85`.
- Browser flow on `/eval-hub` created policy
  `epp_3de38422be714fb2abaea6894378d614` and two immutable versions:
  - v1 required score `>=0.8` plus `OBSERVATION_NAME >=2` buckets. All three
    items passed quality but formed one bucket, so run
    `epr_a1e7cd19a52c48a4b9383f5e44d674d5` returned
    `BLOCKED / insufficient_diversity`.
  - v2 kept score `>=0.8` and changed diversity to `NONE`. The same evidence
    produced run `epr_49c3ef07a5984342b92acde9ef4ff10a` with
    `RECOMMENDED / promotion_criteria_met`.
- After both runs the source batch was still `PENDING_REVIEW`, had no review
  row and had no materialization row. This proves recommendation cannot bypass
  human approval.
- The temporary browser-acceptance user was logged out, demoted to `USER`,
  removed from Namespace membership and disabled. Immutable creator/audit
  provenance remains intact.

## Content and audit boundary

- Both run rows stored `3/3 completed`, `3/3 scored`, `3/3 quality passed`,
  one diversity bucket and 64-character evidence digests.
- `evaluation_promotion_run_items` contains only source refs, three booleans,
  score/diversity digests and a reason code; it has no raw value, comment or
  correction column.
- Database scans across every Promotion audit record and
  `EvaluationPromotionRunCreated` Outbox payload found zero
  `score_value/comment/correction` keys.
- The Outbox uses a scalar `reason_codes_csv` allowlist entry; the API retains
  the typed reason-code list. No provider item array or annotation content is
  emitted.

## Upgrade and migration gates

- Revision `20260803_0050` adds Promotion policy, immutable version, run and
  per-item digest tables.
- Isolated real MySQL passed an empty full-chain upgrade, `0049 → 0050 → 0049
  → 0050`, populated downgrade refusal and a final `alembic check` with no new
  operations. The development database is `0050 (head)`.
- The real Langfuse compatibility gate now creates a fixed numeric Promotion
  Queue/Score Config fixture, writes an `ANNOTATION` score, reads it through
  production Scores API v3 adapter code, verifies quality plus metadata-only
  diversity digests, and cleans up its exact queue item.
- The complete gate passed health/version, Clinic/Observations v2, Annotation
  Queue reconciliation, Scores API v3 Promotion evidence, Dataset source/pin,
  Experiment scores/manifest and direct OTLP v4 ingestion.

## Automated regression

- Backend full suite: `955 passed, 19 skipped`.
- Langfuse upgrade regression lane: `48 passed`, followed by the real server
  compatibility gate PASS.
- Frontend: `44 passed`; ESLint `0 errors` with 9 accepted existing warnings;
  production build PASS.
- Ruff, compile/import and Alembic check: PASS.
- mypy: `82` existing errors in `24` files, equal to the accepted ceiling;
  NEXT-011 adds none.

## Upgrade safety conclusion

Langfuse remains strongly decoupled at the adapter boundary. Upgrading
Langfuse does not migrate DuckDock Promotion governance rows; it requires a
new candidate to pass exact Queue/Score Config shape, Annotation item
reconciliation, Scores API v3 filtering/typing and metadata-only diversity
evidence before the accepted compatibility profile can change.
