# NEXT-007 Trace2Dataset evidence

Date: 2026-07-31
Database revision: `20260731_0046`

## Result

DuckDock now provides the first Langfuse-native data-flywheel slice:

```text
Langfuse Trace/Observation
  -> provider-local input/output copy
  -> deterministic Langfuse Dataset item
  -> pinned provider Dataset timestamp
  -> immutable DuckDock DatasetVersion + materialization receipt
```

DuckDock MySQL does not store the selected input, output or Dataset item
content. `EvaluationDatasetMaterialization` stores only Namespace/Dataset/
Version identities, Trace/Observation and provider item references,
idempotency/request digests, schema identity, actor and timestamp.

## Contract

- `POST /api/v2/evaluation-datasets/{public_id}/trace-materializations`
  requires an `Idempotency-Key`, a 32-hex Trace ID and an optional 16-hex
  Observation ID.
- An omitted Observation selects exactly one root; ambiguous or content-less
  sources fail closed.
- Langfuse v4 `GET /api/public/v2/observations` is called with
  `core,basic,time,io`; raw strings are decoded only in the provider adapter
  and each field is bounded at 1 MiB.
- Observation input becomes Dataset input and Observation output becomes
  expected output.
- Dataset item ID is UUIDv5 over Dataset/Trace/Observation, making provider
  replay safe if the process fails before the MySQL transaction commits.
- The provider Dataset is indexed by item ID/status/update time/source refs;
  its SHA-256 manifest and `max(updated_at) + 1 ms` pin create the immutable
  `EvaluationDatasetVersion`.
- Audit and `EvaluationDatasetMaterialized` Outbox are written in the same
  MySQL transaction and contain no content.

## Real Langfuse and browser lane

The running local Langfuse `4.1.0` / Python SDK `4.14.2` instance and live
DuckDock UI at `http://127.0.0.1:5174/eval-hub` verified:

| Evidence | Value |
|---|---|
| DuckDock Dataset | `eds_b1a2ecf548094cb5824ce93dfddbbc60` |
| Langfuse Trace | `e448b6523c3a8ce123a97d7280b9481e` |
| Langfuse Observation | `0c96564143a78338` |
| Langfuse Dataset item | `df509b29-3132-5dbc-b2d0-828975f899ac` |
| Materialization | `edm_e2410bc10bda43d1859c0e56c8786b95` |
| DatasetVersion | `edv_2ac1f00e21b2492f92a70339774cf4d9` / v3 |
| Pinned item count | `3` |
| Provider version pin | `2026-07-31T07:17:45.195000Z` |
| Manifest digest | `31b5c7dd396cf71d03afa16efdc33281e5e4c4edae74260c9c3c134be1f5ff88` |
| Outbox event | `ee7ee298-5498-487e-8e29-5fa9ba5b1804` / `PUBLISHED` |

The Langfuse item was read back and its source refs plus input/expected-output
were exact. Loading the pinned provider Dataset returned exactly three items.
Submitting the same UI request again returned the original receipt and left
the Dataset version/materialization counts unchanged. The UI showed the
immutable v3/3-items record; application console output contained no errors
(only the two pre-existing React Router v7 warnings).

The browser verification identity was logged out, disabled, stripped of
Namespace membership and assigned a random unusable password after the run.
Its disabled row remains solely because immutable audit/materialization
records retain the original actor FK.

## Migration and regression

- Development MySQL upgraded `0045 -> 0046`; current head is `0046` and
  `alembic check` is clean.
- Isolated MySQL completed `0045 -> 0046 -> 0045 -> 0046`.
- A populated receipt correctly blocked downgrade with
  `0046 downgrade refused`; exact receipt cleanup then allowed the round trip.
- Full backend: `937 passed, 19 skipped`.
- Full frontend: `38 passed`; lint `0 errors` / 9 existing warnings; production
  build passed.
- Ruff and Python compile: clean.
- mypy: `82` errors, equal to the accepted ceiling `82`.
- The live Langfuse compatibility gate passed server/SDK, raw Observation IO,
  Dataset source linkage/version pin, Experiment/manifest, Experiments API and
  direct OTLP lanes.

## Remaining boundary

Batch selection, production sampling policies, annotation queues,
quality/diversity curation and automatic online-to-offline optimization remain
follow-up data-flywheel work. This slice is explicit operator-driven
materialization and does not silently ingest production content.
