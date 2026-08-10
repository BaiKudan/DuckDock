# Eval Hub EH-04 reproducible comparison evidence — 2026-07-31

## Result

EH-04 is complete. DuckDock now governs immutable regression-policy versions
and exact baseline/candidate comparisons without resolving any “latest”
Evaluation, manifest or policy.

Langfuse remains the provider-hosted item-level investigation surface. Its
native Compare View designates a baseline, matches rows by stable Dataset item
identity and exposes score/cost/latency deltas. DuckDock adds the control-plane
layer: explicit immutable pins, tenant-scoped policies, deterministic
release-oriented outcomes and an auditable reproducibility digest.

References:

- [Langfuse baseline Compare View](https://langfuse.com/changelog/2025-11-06-compare-view-baseline-support)
- [Langfuse Experiment data model](https://langfuse.com/docs/evaluation/experiments/data-model)
- [Langfuse score analytics](https://langfuse.com/docs/evaluation/evaluation-methods/score-analytics)

## Governance contract

- Revision: `20260731_0043`.
- New models: `RegressionPolicy`, immutable `RegressionPolicyVersion` and
  immutable `EvaluationComparison`.
- Result manifests now persist their own aggregate score. The migration
  backfills it only when the manifest provider Experiment matches the
  Evaluation's current provider reference.
- A comparison request must name:
  - baseline Evaluation and exact result manifest;
  - candidate Evaluation and exact result manifest;
  - exact RegressionPolicyVersion;
  - Namespace-scoped idempotency key.
- Comparable pins must share DatasetVersion, EvaluatorVersion, target type,
  provider Dataset and result schema.
- Outcome:
  - `PASS`: every score-floor/drop policy check passes;
  - `REGRESSION`: one or more explicit breach flags are true;
  - `INCONCLUSIVE`: either result is partial or an immutable aggregate metric
    is unavailable.
- “latest”, “current” and “newest” are not resolved. An unknown explicit
  manifest returns not found.
- The SHA-256 reproducibility digest includes exact Dataset/Evaluator/Target,
  provider Experiment, result-manifest and policy-version pins plus the
  derived metrics/outcome.
- `EvaluationComparisonCreated` is same-transaction Outbox evidence with an
  explicit content-free allowlist.
- New write endpoints commit before returning so policy → version →
  comparison can be called immediately without a post-response commit race.

## Real Langfuse comparison lane

Baseline:

- Evaluation: `eval_315a848a25ff4109ba28e9a97823e1a4`.
- Manifest: `erm_e7273494238243e89aafbd9ca8b2d327`.
- Provider Experiment: `94d84315e03526ac`.
- Score/pass rate: `0.5 / 0.5`.

Candidate:

- Experiment: `exp_27c1644ccceb443f9962c44f7d2528ac`.
- Evaluation: `eval_d5b5462d180a4fe79af89d979db30b06`.
- Manifest: `erm_969bbbd6c42d4b4f94bb9479c50cc738`.
- Provider Experiment: `3fa1977f5a9a2803`.
- Manifest digest:
  `77332c7afeb3ea27650072bb8b29b9a50461f861510e56a65462c9aa0fdc209d`.
- Result: `COMPLETED/COMPLETE`, score `0.5`, expected/processed/scored
  `2/2/2`, passed/failed/error `1/1/0`, attempts `1`.

Policy and comparison:

- Policy: `rgp_7a9004dd4a094071a3f813aca6cb77da`.
- PolicyVersion: `rpv_12e1a116f5bd48cfa20aef8d54304736`.
- Thresholds: minimum candidate score `0.5`, maximum score drop `0.05`,
  maximum pass-rate drop `0.05`, complete results required.
- Comparison: `cmp_c1e80aaa58f6486f965327b3b058b65c`.
- Outcome: `PASS/comparison_passed`.
- Score delta: `0.0`; pass-rate delta: `0.0`; every breach flag false.
- Reproducibility digest:
  `ba592797ef83fd67ffeec111f77a5e99b3d4b6720cd396411f1cbb3818a5f4dd`.
- `EvaluationComparisonCreated`: `PUBLISHED`, attempt `1`.

The Langfuse Experiments Public API was queried with `fields=core,scores`.
Both provider Experiments contained the exact same two Dataset item IDs and
score set `[0.0, 1.0]`. Reconstructing the complete comparison payload from
the DuckDock HTTP response independently produced the exact stored
reproducibility digest.

No Dataset input, expected output, generated output, trace content or item
array was copied into the comparison record, API response, audit event or
Outbox payload.

## Migration and automated checks

- Isolated real MySQL full upgrade to `0042`, then
  `0042 -> 0043 -> 0042 -> 0043`: pass.
- Existing matching manifest score backfill `NULL -> 0.75`: pass.
- A populated RegressionPolicyVersion caused downgrade to fail closed with
  the documented archival/remediation requirement.
- Development MySQL: `20260731_0043 (head)` and `alembic check` clean.
- EH-04/Evaluation/API/migration focused regression: `47 passed, 1 skipped`.
- Full backend regression: `919 passed, 19 skipped`.
- Ruff, compile/import and OpenAPI: clean.
- Backend, Worker/Beat and Langfuse Web/Worker: healthy.

## Next slice

EH-05 will bind an exact successful EvaluationComparison and its immutable
candidate/Deployment revision to Release Candidate evidence, then enforce
that evidence through the existing provider-neutral Release Gate seam. It
must not accept Namespace-level or “latest” aliases.
