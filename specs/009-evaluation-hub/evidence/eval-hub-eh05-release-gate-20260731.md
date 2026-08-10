# EH-05 Release Candidate evaluation gate evidence

Date: 2026-07-31
Database revision: `20260731_0044`

## Result

EH-05 is complete. DuckDock now binds an immutable
`EvaluationComparison` to an exact release-candidate reference and exact
Deployment public ID/revision. The candidate Release Gate returns `PASS` only
when bound evidence exists and every bound comparison passed. Missing,
`REGRESSION` and `INCONCLUSIVE` evidence return `BLOCKED`.

The legacy Skill/Clinic Gate was not routed through this new path and retains
its previous compatibility behavior.

## Governed contract

- `ReleaseCandidateEvaluationBinding` stores:
  - exact Namespace and release-candidate reference;
  - Deployment FK plus public ID, revision and configuration-digest snapshot;
  - exact immutable `EvaluationComparison` FK;
  - schema identity and canonical SHA-256 binding digest;
  - creator and timestamp.
- Creation rejects `latest`, `newest` and `current` aliases.
- The selected comparison's candidate Experiment must have:
  - `target_ref == release_candidate_ref`;
  - `target_digest == deployment.configuration_digest`.
- A Deployment in `FAILED` or `RETIRED` state cannot receive formal evidence.
- Once a REGISTERED Deployment has a binding, its revision, configuration and
  components cannot be mutated. Activation remains allowed.
- The exact Foundation `ReleaseEvidenceSelector` is the only Gate lookup key.
  No Namespace-latest fallback exists.

Gate outcomes:

| Bound evidence | Gate outcome | Reason |
|---|---|---|
| none | `BLOCKED` | `runtime_evaluation_missing` |
| any `REGRESSION` | `BLOCKED` | `runtime_evaluation_regression` |
| any `INCONCLUSIVE` | `BLOCKED` | `runtime_evaluation_inconclusive` |
| one or more and all `PASS` | `PASS` | `runtime_evaluation_passed` |

## API and security

- `POST/GET /api/v2/release-evidence/bindings`
- `GET /api/v2/release-evidence/bindings/{public_id}`
- `POST /api/v2/release-gates/evaluations`

Binding creation uses a required Namespace-scoped `Idempotency-Key`. The
same transaction writes audit plus a low-sensitive
`ReleaseCandidateEvaluationBound` Outbox event. The event contains only
candidate/Deployment/comparison identifiers, outcomes and digests. It does
not contain Dataset items, prompts, outputs, score reasons or trace content.

## Real MySQL migration lane

An isolated `duckdock_eh05_test` MySQL 8.4 database completed:

1. empty full-chain upgrade to `0043`;
2. `0043 -> 0044`;
3. `0044 -> 0043 -> 0044`;
4. populated downgrade refusal with:
   `0044 downgrade refused: release evaluation bindings require an explicit archival/remediation plan`;
5. exact test-row removal followed by a successful downgrade/upgrade;
6. `alembic current == 20260731_0044 (head)`.

The first real run also caught and corrected a MySQL 64-character identifier
limit before the migration reached the development database. All 0044 indexes
now use explicit bounded names. The development database then upgraded to
0044 and `alembic check` reported no schema drift.

## Real Langfuse-backed release lane

EH-05 reused the immutable EH-04 comparison whose two Langfuse Experiment
runs had matching stable Dataset item IDs and provider scores.

| Evidence | Value |
|---|---|
| EvaluationComparison | `cmp_c1e80aaa58f6486f965327b3b058b65c` |
| Comparison outcome | `PASS` |
| Candidate target ref | `provider://dataset-replay` |
| Deployment | `dep_2b5d3b65476a4894bfe20c747493c837` |
| Deployment revision | `eh05-real-1` |
| Binding | `rcb_1d5353555b964bc6a88b1ff9271725bc` |
| Binding digest | `97dcfa44d806051ea2972fbca1145e60554e17ae2625c7e07c4c259e145fd93a` |
| Gate outcome | `PASS` |
| Gate reason | `runtime_evaluation_passed` |
| Gate decision digest | `bfe2f8fb100ef53103686405c4d51ba326724632c160367df62e7855ca1fbdd9` |
| Binding Outbox | `PUBLISHED`, attempt `1` |

The binding POST and immediate GET returned identical representations. The
Gate digest was independently reconstructed solely from the HTTP response and
exact selector and matched the stored response digest. Re-evaluating the same
Deployment revision with the unbound
`provider://unbound-candidate` reference returned
`BLOCKED/runtime_evaluation_missing`, proving there is no latest-value
fallback.

## Verification

- EH-04/EH-05 focused regression: `20 passed`.
- Release/deployment compatibility lane: `26 passed, 1 skipped`.
- Full backend regression: `924 passed, 19 skipped`.
- Ruff, compile, FastAPI import and OpenAPI generation: clean.
- Required 0044 API paths are present in generated OpenAPI.
- Development backend, frontend, MySQL, Redis and Langfuse remain healthy.
