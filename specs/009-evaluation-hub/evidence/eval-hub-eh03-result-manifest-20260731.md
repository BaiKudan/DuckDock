# Eval Hub EH-03 result governance evidence — 2026-07-31

## Result

EH-03 is complete. DuckDock now records an immutable metadata manifest for
each provider-hosted Evaluation result, distinguishes complete results from
partial provider result loss and exposes a content-free result history API.

This design follows the Langfuse Experiment data model: an Experiment
(`DatasetRun`) owns item results that reference provider-hosted traces and
scores. The Langfuse SDK documents that `item_results` contains successfully
processed items, while failed items are excluded and logged. DuckDock
therefore does not infer that a missing item or missing score is a failed
quality assertion. References:

- [Langfuse Experiment data model](https://langfuse.com/docs/evaluation/experiments/data-model)
- [Langfuse Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
- [Langfuse Experiments Public API](https://langfuse.com/changelog/2026-07-07-experiments-public-api-and-mcp)

## Governance contract

- Revision: `20260731_0042`.
- New immutable model: `EvaluationResultManifest`.
- Identity: Namespace, Evaluation, monotonic version, provider Dataset and
  Experiment references.
- Integrity: schema name/version plus canonical SHA-256 digest.
- Counts: expected, processed, scored, passed, failed and error.
- Complete result: `Evaluation.status=COMPLETED`,
  `result_completeness=COMPLETE`, `error_count=0`.
- Partial result: `Evaluation.status=PARTIAL`,
  `result_completeness=PARTIAL`, `error_count>0` and one safe loss code:
  `provider_item_loss`, `provider_score_loss` or
  `provider_item_and_score_loss`.
- Retry: `FAILED` and `PARTIAL` Evaluations may be retried. Current operational
  fields reset, but previous manifests remain immutable; the next accepted
  result receives the next manifest version.
- Eventing: `EvaluationResultManifestRegistered` is written in the same
  transaction with an explicit low-sensitivity field allowlist.
- API: `GET /api/v2/evaluations/{public_id}/result-manifests` returns only
  identity, digest, counts, completeness and timestamps.

MySQL and the public API do not contain Dataset input, expected output,
generated output, trace content, item arrays, evaluator reasons or prompt
text. Those remain provider-hosted in Langfuse.

## Real Langfuse/Worker lane

Runtime:

- DuckDock backend: `http://127.0.0.1:8801`, healthy.
- DuckDock Worker/Beat: running and restarted from the EH-03 source.
- Langfuse Web/Worker: v4.1.0, healthy.
- Langfuse Python SDK: v4.14.2.
- Dataset: `cms8a7yxj0006pd07xe5s18s6`
  (`duckdock.ns1.evalhub-v4-smoke-20260731`).

Accepted run:

- Experiment: `exp_e9712b7cb8d54744a1cdc7894fb47a31`.
- Evaluation: `eval_315a848a25ff4109ba28e9a97823e1a4`.
- Execution key: `eh03:real:manifest:v1`.
- Provider Experiment: `94d84315e03526ac`.
- Result manifest: `erm_e7273494238243e89aafbd9ca8b2d327`,
  version `1`.
- Result: `COMPLETED/COMPLETE`, score `0.5`, expected `2`, processed `2`,
  scored `2`, passed `1`, failed `1`, error `0`, attempts `1`.
- Manifest digest:
  `df2eb16201ad04923620973f1647367619842ba161dd2a3432d91b3457b237f8`.
- Both `EvaluationQueued` and `EvaluationResultManifestRegistered` reached
  `PUBLISHED` on attempt `1`.

The Langfuse Experiments Public API was queried with `fields=core,scores`; it
returned exactly two provider items and scores `[0.0, 1.0]`. Reconstructing
the canonical result-reference manifest from provider item IDs, trace IDs and
scores produced the exact stored SHA-256 digest. No `io` field was requested.

The live DuckDock HTTP API returned the manifest and current manifest pointer
with RBAC authentication. Its response field set contained no `input`,
`output`, `expected_output` or `items`.

## Migration and automated checks

- Isolated real MySQL full upgrade to `0041`, then
  `0041 -> 0042 -> 0041 -> 0042`: pass.
- A deliberately old `FAILED` row with total/pass/fail=`5/2/1` migrated to
  processed/scored/error=`3/3/2`, proving historical non-completed summaries
  satisfy the new invariant.
- Empty-result downgrade passed. Populated manifest/partial downgrade is
  guarded and requires an explicit archival/remediation plan.
- Development MySQL: `20260731_0042 (head)` and `alembic check` clean.
- EH-03/API/migration focused regression: `33 passed`.
- Full backend regression: `914 passed, 19 skipped`.
- Ruff and Python compile/import: clean.
- DuckDock backend and Langfuse health checks: pass.

## Next slice

EH-04 will add exact baseline comparison, regression policy and reproducible
comparison views on top of immutable Dataset/Evaluator/Target/Result pins. It
must not use “latest Evaluation” as release evidence.
