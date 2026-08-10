# NEXT-009 reproducible production sampling evidence

Date: 2026-07-31
Database revision: `20260731_0048`

## Result

DuckDock now freezes metadata-only production sampling rules as immutable
versions and executes them as deterministic, content-free selection snapshots:

```text
explicit policy version + Dataset + time window
  -> metadata-only Langfuse candidate query
  -> always exclude already-governed sources
  -> stable-hash rank and minimum-size guard
  -> immutable sampling run + ranked source references
  -> existing immutable PENDING_REVIEW curation batch
  -> independent human approval and provider-local materialization
```

No provider input/output is requested during sampling. Policy, run, Audit and
Outbox records contain only filters, identifiers, bounded counts and digests.
The existing NEXT-008 review gate remains mandatory.

## Contract delivered

- Namespace-scoped `EvaluationSamplingPolicy` with immutable versions.
- Exact version pin, explicit Dataset and explicit bounded time window.
- `STABLE_HASH` ranking over policy digest, Dataset and source references.
- Target size `1..20`, minimum-size fail-closed guard and candidate bound
  `sample_size..100`.
- `exclude_governed=true` is a database invariant, not an optional runtime
  preference.
- Idempotency by Namespace/key plus run-digest replay protection.
- Immutable run/item snapshot and an automatically created existing
  `EvaluationDatasetCurationBatch` in `PENDING_REVIEW` state.
- Namespace RBAC/cross-Namespace non-enumeration, Audit and transactional
  `EvaluationSamplingRunCreated` Outbox event.
- Eval Hub UI for policy creation, versioning, exact-version execution and
  recent-run evidence.

## Real Langfuse and browser lane

The local DuckDock dev UI was exercised in the in-app browser against the
user's running self-hosted Langfuse `4.1.0` with Python SDK `4.14.2`. Five new
root observations named `duckdock.sampling.e2e.20260731` were ingested with
real IO in Langfuse. The UI created and executed this policy:

| Evidence | Value |
|---|---|
| Policy | `esp_dff0018084604f5392c9570378f608c0` |
| Policy version | `esv_c9ec1548642c4c77b637d8eac9edc8a5` / v1 |
| Configuration | `STABLE_HASH`, target 3, minimum 3, limit 100, root-only |
| Config digest | `1d3d6ab53c31e89cb7329a3ae15a78af8e2a3e03c6468ea8952ea72933d34fcf` |
| Sampling run | `esr_164634dcc6b6427da43d8cc7fe160e7f` |
| Candidate / eligible / selected | `5 / 5 / 3` |
| Selection digest | `5c39593625ce063dde5ff4e596d22b247bac9440c84168eb1d49de4d15a060be` |
| Run digest | `5b7094b9fc71fbe50471f82adbfb4c02acf854512e60b5581545dd6847880180` |
| Review batch | `ecb_1aaa4fc5a3344e6497ed10e18cb17c61` / `PENDING_REVIEW` |

Stable-hash rank order was:

1. `d0a81f600ca2a3abec11c6f4f9f6f2fa` / `7b2094c1379f34f4`
2. `418e71ef9d04bf30a7b0e119c5091bb3` / `365597d553dc77e0`
3. `1c97870bb5cf8ac20a5e8fe0ba7efbea` / `afe843659c557747`

The browser then repeated the metadata-only candidate query. Those three
sources were visibly disabled as `已治理`; the two unselected observations
remained selectable. The batch was intentionally left pending to prove that
sampling does not bypass review. `EvaluationSamplingRunCreated` was
`PUBLISHED`; JSON inspection found no `input`, `output` or `name` key in the
Outbox or Audit payload. The temporary browser user was logged out, disabled,
stripped of Namespace membership and reset to the ordinary user role.

## Migration and regression

- Development MySQL is at `0048` head and `alembic check` is clean.
- An isolated real MySQL database completed empty
  `0047 -> 0048 -> 0047 -> 0048`.
- A populated sampling policy refused downgrade with
  `0048 downgrade refused`; the isolated database was then dropped.
- Full backend: `947 passed, 19 skipped`.
- Full frontend: `42 passed`; lint `0 errors` / 9 existing warnings;
  production build passed.
- Ruff: clean.
- mypy: `82` errors, equal to the accepted repository baseline; no error
  points to NEXT-009 code.
- Live Langfuse v4 compatibility gate: `44 passed`.
- Backend, frontend, Langfuse Web/Worker and supporting development services
  remain running.

## Langfuse reuse boundary

Langfuse already provides native Annotation Queues and a public queue API, so
DuckDock should reuse that capability through a replaceable adapter in the
next slice. It is deliberately not part of the sampling database transaction:
a remote queue write can succeed while MySQL fails. DuckDock therefore owns
the immutable sampling/review provenance, while future queue synchronization
will be independently idempotent and upgrade-gated.

Remaining work is annotation-queue synchronization plus quality/diversity
selection and closed-loop re-evaluation.
