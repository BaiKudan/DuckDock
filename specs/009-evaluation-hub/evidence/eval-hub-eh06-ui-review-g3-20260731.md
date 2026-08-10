# EH-06 Eval Hub UI, human review and G3 technical evidence

Date: 2026-07-31
Database revision: `20260731_0045`

## Result

EH-06 is complete and formally accepted at G3. DuckDock now
provides a management UI over the governed Langfuse-first evaluation records,
reproducible comparisons, exact release-candidate bindings and candidate
Release Gate decisions.

Product/Architecture Owner explicitly replied “批准，继续继续” on 2026-07-31;
G3/M3, S5-S6 and E03 are formally closed.

## Human-review contract

`ReleaseCandidateEvaluationReview` records one final human decision for one
exact `ReleaseCandidateEvaluationBinding`:

- decisions are `APPROVED` or `REJECTED`;
- the binding FK is unique, so a final decision cannot be replaced;
- a rejection requires a comment of at least five characters;
- Namespace plus idempotency key is unique and exact retries are safe;
- the canonical review digest includes binding digest, decision, normalized
  comment and reviewer;
- review creation writes audit and
  `ReleaseCandidateEvaluationReviewed` Outbox in the same transaction;
- Outbox exposes identifiers, decision and digests only; the review comment is
  not included.

Gate semantics remain compatible with EH-05 when no review exists. An
`APPROVED` review adds `manual_review:pass` evidence. A `REJECTED` review adds
`manual_review:fail` and blocks with `manual_review_rejected`. Automatic
comparison regression and inconclusive reasons remain distinct.

## UI and API

The management route `/eval-hub` contains:

- Namespace-scoped Dataset/Evaluator, completed-run, passing-comparison and
  reviewed-binding metrics;
- a governance overview from provider assets through final review;
- reproducible baseline/candidate score and pass-rate comparison cards;
- exact candidate, Deployment revision/config digest, Comparison and binding
  digest views;
- immutable approve/reject controls for unreviewed bindings;
- exact-selector Gate re-evaluation with reason codes, evidence kinds/count
  and decision digest.

New APIs:

- `POST /api/v2/release-evidence/reviews`
- `GET /api/v2/release-evidence/reviews`

Binding responses now include their optional final review. The generated
FastAPI OpenAPI contract explicitly declares both operations, the required
`Idempotency-Key`, review request schema and decision enum.

## Real MySQL migration lane

An isolated `duckdock_eh06_test` MySQL 8.4 database completed:

1. empty full-chain upgrade through `0044`;
2. `0044 -> 0045`;
3. `0045 -> 0044 -> 0045`;
4. populated downgrade refusal with
   `0045 downgrade refused: release evaluation reviews require an explicit archival/remediation plan`;
5. exact guard-row removal followed by successful downgrade/upgrade;
6. `alembic current == 20260731_0045 (head)`;
7. `alembic check` with no model/schema drift.

The development database is also at `0045 (head)` with a clean Alembic check.

## Real Langfuse-backed review and Gate lane

EH-06 reviewed the existing EH-05 binding, which points to the immutable
Langfuse-backed EH-04 Comparison.

| Evidence | Value |
|---|---|
| EvaluationComparison | `cmp_c1e80aaa58f6486f965327b3b058b65c` |
| Release binding | `rcb_1d5353555b964bc6a88b1ff9271725bc` |
| Deployment | `dep_2b5d3b65476a4894bfe20c747493c837` |
| Deployment revision | `eh05-real-1` |
| Review | `rcr_a6ef54dac0d6467c989119fb962c319b` |
| Review decision | `APPROVED` |
| Review digest | `05c91a168f72b912bb0a769f621a89bf8b391ec9ec710b6ba3d45a1f9a8d62fd` |
| Gate outcome/reason | `PASS` / `runtime_evaluation_passed` |
| Gate evidence | `evaluation_comparison:pass`, `manual_review:pass` |
| Gate decision digest | `ccb44281a7303b0e5e2e321b592e8d4816a7f8ef2e6b9a3061ed84e94cf6bb69` |
| Review Outbox | `PUBLISHED` |

The review was submitted through the live HTTP API as the existing
`ddadmin` user. The resulting audit action is
`release_candidate.evaluation_reviewed`.

## Real browser verification

The in-app browser used the running development frontend at
`http://127.0.0.1:5174` and verified:

1. authenticated management navigation exposes `Eval Hub`;
2. overview shows one Dataset/Evaluator pair, five complete runs, five passing
   Comparisons and one reviewed binding;
3. comparison cards expose exact baseline/candidate manifests, scores, policy
   versions and reproducibility digests;
4. the release card shows the exact Deployment revision, Comparison,
   `APPROVED` review digest and review comment;
5. clicking `复算门禁` renders `PASS`,
   `runtime_evaluation_passed`, two evidence records and digest
   `ccb44281a7…4cf6bb69`.

No application console error occurred. The only browser warnings were the two
existing React Router v7 future-flag notices. The temporary browser-only
administrator was removed after verification.

## Verification

- EH-06 focused backend contract/migration regression: `7 passed`.
- Full backend regression: `927 passed, 19 skipped`.
- Full frontend regression: `37 passed`.
- Frontend production build: passed.
- ESLint: `0 errors`, 9 pre-existing warnings.
- Ruff and Python compile: clean.
- Backend, Worker, Beat, MySQL, frontend, Langfuse Web/Worker and ClickHouse
  remained live; DuckDock backend health returned `200`.

## Remaining boundary

- Product/Architecture Owner G3 approval is recorded; EH-06 and M3 are closed.
- Dataset item upload/materialization was deferred from EH-06 and was
  subsequently implemented as NEXT-007 Trace2Dataset in revision 0046.
- Environment promotion, canary and rollback belong to E05 Release Control,
  not EH-06.
