# Eval Hub EH-02 execution evidence — 2026-07-31

## Result

EH-02 is complete. DuckDock now schedules Langfuse-backed Evaluations through
the transactional Outbox and Celery, protects execution with a renewable
database lease, retries with bounded exponential backoff, supports
cooperative cancellation and stores only a bounded result summary.

The implementation uses the Langfuse v4 high-level Experiment API described
by the official
[SDK experiment documentation](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk).
Provider-hosted Dataset versions are pinned by timestamp, consistent with
Langfuse's
[versioned Dataset experiment support](https://langfuse.com/changelog/2026-02-11-versioned-dataset-experiments).

## Execution contract

- Revision: `20260731_0041`.
- Dispatch: same-transaction `EvaluationQueued` Outbox event plus a recovery
  Beat scan for due/expired Evaluations.
- Idempotency: Namespace-scoped `execution_key`; duplicate task delivery is a
  lease-protected no-op.
- Recovery: row lock, lease owner/expiry, heartbeat and expired-lease reclaim.
- Retry: bounded attempts and exponential delay; terminal failure can be
  explicitly retried.
- Cancellation: immediate while pending and cooperative while running.
- Provider boundary: pinned Langfuse Dataset version and high-level Experiment
  runner; target execution is an adapter port.
- Storage boundary: MySQL receives provider ref, score/count summary, attempts
  and operational timestamps only. Item input, expected output, generated
  output, trace content and item-level score detail remain in Langfuse.

## Real Langfuse and Worker lane

Runtime:

- DuckDock backend: `http://127.0.0.1:8801`, healthy.
- DuckDock Worker/Beat: running with `dispatch_due_evaluations` and
  `run_evaluation` registered.
- Langfuse Web/Worker: v4.1.0, healthy.
- Langfuse Python SDK in the final DuckDock image: v4.14.2.
- Langfuse: `http://127.0.0.1:3200`.
- Dataset: `cms8a7yxj0006pd07xe5s18s6`
  (`duckdock.ns1.evalhub-v4-smoke-20260731`).

Pinned successful mixed-result run:

- DatasetVersion: `edv_87f100dda00b413f91e790eb10625225`.
- Provider pin: `2026-07-31T03:01:42.364520Z`.
- Experiment: `exp_d0398d05354340fb8e3b1a1cc9688404`.
- Evaluation: `eval_49f8955b7d9d4e05b10d06de9e7c1c89`.
- Execution key: `eh02:real:langfuse:replay:v2`.
- Provider Experiment: `9b4ebec5fe69f9d5`.
- Result: `COMPLETED`, score `0.5`, total `2`, passed `1`, failed `1`,
  attempts `1`.

Both Outbox and recovery Beat delivered the same stable Celery task ID. The
Worker log received the duplicate twice, while the database lease allowed
exactly one execution and one result acknowledgement.

After rebuilding backend/worker/beat from the final source and dependency
lock range, a second post-rebuild lane also passed:

- Experiment: `exp_f1157f7fdd374643aa8192bb788bde16`.
- Evaluation: `eval_1a6cabd04b644852bc5545f4313560f2`.
- Provider Experiment: `6138673cd4140af8`.
- Result: `COMPLETED`, score `0.5`, total `2`, passed `1`, failed `1`,
  attempts `1`.
- Langfuse Experiments API confirmed the exact provider experiment, Dataset ID
  and item count.

An earlier type-boundary run is retained as negative evidence:
`eval_bc365b995b4047bc85d0b15436cd1033` completed with score `0.0`.
Langfuse had materialized the expected value as numeric `4`, while the replay
output was string `"4"`; the type-strict exact-match evaluator correctly
failed it. The pinned v2 run used numeric `4` on both sides and produced the
expected one-pass/one-fail aggregate.

## Migration and automated checks

- Isolated real MySQL full upgrade from baseline to `0041`: pass.
- Isolated real MySQL `0041 -> 0040 -> 0041`: pass.
- `evaluations.public_id` remained `VARCHAR(40)` across the EH-02 round trip.
- Isolated and development database `alembic check`: no pending operations.
- EH-02 focused regression: `32 passed`.
- Full backend regression: `913 passed, 19 skipped`.
- Ruff: clean.
- Compile/import/OpenAPI: clean.
- DuckDock backend, frontend and Langfuse health checks: pass.

The temporary migration database was deleted after verification. The
development database remained at `20260731_0041`, and the real Evaluation
evidence above was not modified by the migration round trip.

## Remaining Eval Hub work

- EH-03: governed provider-hosted case/result artifact references and explicit
  partial-result semantics.
- EH-04: baseline comparison, regression policy and reproducibility views.
- EH-05: exact Release Candidate evidence binding and Release Gate enforcement.
- EH-06: Eval Hub UI, human review and G3 acceptance evidence.
