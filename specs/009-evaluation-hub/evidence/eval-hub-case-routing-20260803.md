# NEXT-012 — cross-batch Golden/Bad Case routing evidence

Date: 2026-08-03
Result: PASS

## Accepted boundary

- DuckDock owns a Namespace-scoped case-routing policy and immutable versions.
  Every version pins one exact Promotion policy version whose diversity
  dimension is not `NONE`.
- One run accepts one to twenty distinct, non-overlapping Promotion runs from
  that exact version. It consumes only persisted `score_present`,
  `quality_passed`, `diversity_bucket_present` and digest evidence; it does
  not call Langfuse or read raw score values.
- Quality passes enter the Golden lane. Scored quality failures enter the Bad
  Case lane. Missing score or bucket evidence is retained as `EXCLUDED`.
- Selection uses stable rank digests and cluster round-robin over frozen
  `diversity_bucket_digest` values. Either minimum shortfall makes the whole
  run `BLOCKED` and creates no partial curation batch.
- `ROUTED` creates two existing curation batches, both `PENDING_REVIEW`.
  Golden and Bad Case approval/materialization are independent and routing can
  never create an APPROVED review or DatasetVersion.

## Real Hermes, Langfuse and browser acceptance

- Langfuse Web/Worker: `4.1.0`; Python SDK: `4.14.2`.
- Reused Promotion Queue `cmscpapod0024oz07i016rxte`, Score Config
  `a1662d66-6e48-49af-83c1-b286e0a76963` and DuckDock binding
  `eaq_e66823426eea4dc6871748bc6c5ca33a`.
- The browser dispatched a second real Hermes batch
  `ecb_cc87a6931a024ea7ad5518c005be32b4` as
  `ead_00bba49b4b3b4ea4ae3d62397ebc18ac`. Three unique provider queue items
  reconciled to `SYNCED 3/3` and `COMPLETED 3/3`.
- The three new `ANNOTATION` scores produced one quality pass and two quality
  failures. Promotion v1 run `epr_0190872feb36478389420937382957c9`
  therefore returned `BLOCKED` with `1/3` quality passed and one frozen
  `duckdock.curation.e2e.20260731` bucket.
- Routing combined that run with non-overlapping v1 run
  `epr_a1e7cd19a52c48a4b9383f5e44d674d5`, whose three quality passes use the
  distinct `duckdock.sampling.e2e.20260731` bucket.
- Two synchronized Langfuse targets were created:
  - Golden `eds_b479b14e666042d78e0f1697b44fc8a2` /
    provider `cmscrfgmd003eoz0710iacv4t`.
  - Bad Case `eds_f429e4bbdc0a46be9e3db149dad42fa2` /
    provider `cmscrfgne003hoz07omtzwrff`.
- Browser flow created policy `ecrp_fa53f10f538648f8ae920051cd9114b1`,
  immutable version `ecrv_0e3ffd309c2d4525b2a91c5c2c6426fa`, and run
  `ecrr_8eacce1b23a34e53b35c58f98e1ee5e1`.
- The run was `ROUTED`: six candidates, Golden `4/4` across two clusters, Bad
  Case `2/2` in one cluster and `EXCLUDED 0`.
- Generated batches were Golden `ecb_bcc638e276cb454d9554660ad43d425d`
  and Bad Case `ecb_c0ffdc64912a43749e38f590b04729a9`. Both remained
  `PENDING_REVIEW`, with zero review and zero materialization rows.
- The temporary browser user was logged out operationally, demoted to `USER`,
  removed from all Namespace memberships and disabled. Immutable creator and
  audit provenance remains intact.

## Determinism, privacy and audit evidence

- The two source runs pin exact Promotion version
  `epv_a663c18dad1f4c80bc832f55e5bf4ee7`; cross-version sources, repeated runs
  and overlapping Observation refs are rejected.
- `evaluation_case_routing_run_items` recorded four selected Golden items in
  two distinct cluster digests and two selected Bad Case items in one digest.
- Request, evidence, routing, cluster and rank digests are all content-free
  SHA-256 values. The routing service never imports or invokes a Langfuse
  adapter.
- `EvaluationCaseRoutingRunCreated` Outbox and
  `evaluation_case_routing_run.created` Audit payloads contained only exact
  pins, Dataset/batch refs, counts, reason codes and digests. Database checks
  found no raw score (`0.95`) or comment field/value.

## Migration and compatibility gates

- Revision `20260803_0051` adds policy, immutable version, run and per-item
  lane/rank tables. Development MySQL is `0051 (head)` and `alembic check`
  reports no new operations.
- Isolated MySQL passed full-chain upgrade to `0050`, `0050 → 0051 → 0050 →
  0051`, and final schema check. A populated policy row caused downgrade to
  refuse with the expected provenance guard; the exact temporary database was
  then deleted.
- The Langfuse upgrade gate now includes Promotion and Case Routing regression
  suites. The real gate passed `54` local tests plus server health/version,
  Clinic/Observations v2, Annotation Queue, Scores API v3, Dataset source/pin,
  Experiment/manifest and direct OTLP v4 lanes.
- The gate also exposed and fixed a clock-bound Annotation Queue test: worker
  time is now relative to the created dispatch's `available_at`, so the test
  cannot begin failing after a fixed wall-clock hour.

## Automated regression

- Backend full suite: `959 passed, 19 skipped`.
- Frontend: `45 passed`; ESLint `0 errors` with 9 accepted existing warnings;
  production build PASS.
- Focused routing backend: `3 passed`; focused Eval Hub UI: `10 passed`.
- Ruff and Alembic static/migration contracts: PASS.
- mypy: `82` existing errors in `24` files, equal to the accepted baseline;
  NEXT-012 adds none.

## Upgrade safety conclusion

NEXT-012 strengthens rather than weakens Langfuse decoupling. Provider-specific
score and annotation semantics stop at NEXT-011; cross-batch routing operates
only on DuckDock's immutable evidence projection. A Langfuse upgrade therefore
does not migrate routing rows or require routing code changes, while the
compatibility gate still verifies the upstream evidence adapter before new
Promotion evidence can enter the routing layer.
