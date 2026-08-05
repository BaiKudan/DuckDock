# NEXT-008 governed Trace2Dataset curation queue evidence

Date: 2026-07-31
Database revision: `20260731_0047`

## Result

DuckDock now provides an operator-governed batch curation loop on top of the
NEXT-007 provider-local Trace2Dataset path:

```text
Langfuse metadata-only candidate query
  -> immutable 1..20 source selection
  -> immutable APPROVED / REJECTED review
  -> provider-local bounded IO copy
  -> deterministic Langfuse Dataset items
  -> one pinned DuckDock DatasetVersion + immutable batch receipt
```

Candidate discovery, selection, review, audit, Outbox and DuckDock API
responses contain no input, output, expected output or tool payload. Raw IO is
requested only after approval and exists ephemerally inside the Langfuse
adapter while it copies content from Langfuse Observation to Langfuse Dataset.

## Governed contract

- `GET /api/v2/evaluation-trace-candidates` supports a bounded time window,
  exact observation name, environment, observation type and root-only
  filtering. It requests only `core,basic,time`.
- `POST /api/v2/evaluation-datasets/{dataset}/curation-batches` accepts one to
  twenty explicit source references and a required idempotency key. Canonical
  source ordering produces an immutable selection digest.
- `POST /api/v2/evaluation-dataset-curation-batches/{batch}/reviews` records
  exactly one immutable APPROVED or REJECTED review.
- Only an approved batch can be materialized. Rejected and unreviewed batches
  fail closed.
- Batch materialization validates every source through the provider adapter,
  writes deterministic UUIDv5 Dataset items, then records the whole batch as
  one immutable DatasetVersion and one immutable materialization receipt.
- Submit, review and materialize each emit a same-transaction metadata-only
  Audit row and Outbox event.
- The Eval Hub UI provides candidate filters, multi-select, review queue,
  rejection, approval and safe materialization retry. A review-commit
  visibility race found during the live run is handled by one bounded retry
  using the same idempotency key.

## Real Langfuse and browser lane

The running local DuckDock UI and the user's real self-hosted Langfuse
`4.1.0` / Python SDK `4.14.2` instance were exercised through the browser.
Three new root observations named `duckdock.curation.e2e.20260731` were
searched, selected, submitted, approved and materialized:

| Evidence | Value |
|---|---|
| Trace / Observation 1 | `4d0119e00579e6a3ce5da66dec7f8f70` / `c8aef3829b46d0cc` |
| Trace / Observation 2 | `ac42e64d4bfae00a9758ffb9aee08ee8` / `563a51316dfabf8d` |
| Trace / Observation 3 | `2082687dbe8576d4730a255da5e87da0` / `4a35e1abf3cb7339` |
| Curation batch | `ecb_cc87a6931a024ea7ad5518c005be32b4` |
| Selection digest | `75b476aa716590a20b1b7135416666e59781877cca5c5efecb5ce59aa9f0c0b9` |
| Review | `ecr_424e02f8c13d49a89d863396c2a691da` / `APPROVED` |
| Review digest | `134ec10cba055cba890f206acf7be8d936cc0861c47a029e555a952e939a47c7` |
| Materialization | `ecm_1dff3f993c444ed7b0756339feeb50a1` |
| DatasetVersion | `edv_a29054ad161146bbbcb637f1416b7ee9` / v4 |
| Provider pin | `2026-07-31T09:26:42.358000Z` |
| Manifest digest | `dbd484e39e1e140931af6c02745812fd7757a0ca57edb3a22997d3991b99e034` |
| Selected / pinned total | `3 / 6` |

The three deterministic Langfuse Dataset item IDs are:

- `22f9adea-7593-5c69-8f68-7fec859cd1e9`
- `9095f897-35e9-5f94-9d5e-c807b8cbbcbf`
- `60fff6a9-d783-5df5-b427-5b3e2f0d497a`

All three items were read back from Langfuse with exact source references,
input and expected output. The post-materialization candidate API returned the
same three metadata records with `already_materialized=true` and no IO fields.
MySQL contained one approved review, three source-to-item mappings and one
v4/6-item DatasetVersion. All submitted/reviewed/materialized Outbox events
were `PUBLISHED`; the corresponding Audit/Outbox payloads did not contain raw
content.

The browser session was logged out after verification. Its temporary user was
disabled, stripped of Namespace membership and reset to the ordinary user
role. The user row remains because immutable governance records retain the
actor foreign key.

## Migration and regression

- Development MySQL is at `0047` head; `alembic check` reports no new upgrade
  operations.
- An isolated real MySQL database completed empty
  `0046 -> 0047 -> 0046 -> 0047`.
- A populated curation batch correctly refused downgrade with
  `0047 downgrade refused`; the exact isolated test database was then dropped.
- Full backend: `943 passed, 19 skipped`.
- Full frontend: `40 passed`; lint `0 errors` / 9 existing warnings;
  production build passed.
- Ruff: clean.
- mypy: `82` errors from the repository's `backend/` ratchet command, equal to
  the accepted baseline `82`; no error points to NEXT-008 code.
- Live Langfuse compatibility gate: `44 passed`, including metadata-only
  candidate query, raw Observation IO, source linkage/version pin,
  Experiment/manifest, Experiments API and direct OTLP lanes.
- DuckDock backend health and Langfuse health/version checks passed; the local
  development environment remains running.

## Remaining boundary

NEXT-008 deliberately stops at explicit operator selection and immutable human
review. Production sampling policies, annotation/label queues, quality and
diversity ranking, automated online-to-offline promotion and closed-loop
re-evaluation remain follow-up data-flywheel work.
