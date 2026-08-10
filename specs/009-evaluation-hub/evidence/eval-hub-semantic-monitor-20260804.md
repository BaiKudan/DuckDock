# NEXT-017 Scheduled Semantic Drift Monitor Evidence — 2026-08-04

## Outcome

NEXT-017 turns the NEXT-016 offline semantic regression comparison into a
durable, explicitly pinned monitoring loop:

1. a Namespace-scoped monitor freezes one `CLUSTERED` baseline run, one
   candidate semantic clustering policy version, one regression policy version
   and a schedule;
2. Celery Beat discovers due monitors while the Worker leases each immutable
   run, reclusters the exact original Case Routing evidence, and creates a new
   immutable regression comparison;
3. `PASS` completes silently, while `DRIFTED`, `INCONCLUSIVE`, and terminal
   execution failure open an in-app alert;
4. alerts have a one-time acknowledgement with an optional operator note; the
   note is stored only on the alert and is excluded from Audit and Outbox;
5. pause/resume and run-now are explicit control-plane operations. No result
   modifies an Experience, Agent, Deployment, Langfuse object, or runtime.

Langfuse and Hermes remain replaceable provider adapters. The monitor stores
only exact DuckDock pins, bounded outcomes, reason codes, counts, and digests.

## Implementation boundary

Revision `20260804_0056` adds:

- `evaluation_semantic_monitors`
- `evaluation_semantic_monitor_runs`
- `evaluation_semantic_monitor_alerts`
- API v2 create/list/get, pause/resume, run-now, run history, alert list, and
  alert acknowledgement surfaces
- Beat due-dispatch plus lease/retry/backoff Worker tasks
- content-free created/queued/completed/opened/acknowledged Outbox events
- the `定时语义漂移监控` Eval Hub panel

The existing semantic clustering public API keeps its duplicate-evidence guard.
Only the internal monitor execution path can intentionally create a fresh
receipt for the same exact source evidence, and that behavior is covered by a
dedicated service test.

## Automated evidence

- Backend full suite: `973 passed, 19 skipped`.
- Focused monitor service/API/migration suite: `15 passed, 1 skipped`.
- Frontend full suite: `49 passed`; the Eval Hub test drives create, run-now,
  pause/resume, run rendering, and alert acknowledgement.
- Frontend lint: `0 errors`, `9` pre-existing warnings; production build passed.
- Targeted Ruff, Python compile/import, and mypy checks passed. Repository-wide
  mypy remains at the unchanged `82`-error baseline.
- Development MySQL reports `20260804_0056 (head)` and `alembic check` reports
  no new upgrade operations.

## Real Beat + Worker + Hermes validation

The rebuilt development Beat discovered the due monitors without a manual task
call. The Worker used the local Mac Hermes OpenAI-compatible embedding endpoint
at `host.docker.internal:50070/v1` with `bge-small-en-v1.5` and completed both
lanes on attempt 1.

Stable lane:

- monitor: `esmp_0fa8286818bf4dd1a0f899498dd932c3`
- run: `esmr_53ac186baf814bb2ab16750e2596eef2`
- candidate semantic run: `escr_3f30d2b3becb47ff83d4a4a23c182118`
- comparison: `esrc_547aa0fd255e4de1a4ffd89cffe6c7fb`
- result: `COMPLETED / PASS`
- alert: none

Deliberately drifted lane:

- monitor: `esmp_3ecd5e5ca4d645d581ef9fed69d886e2`
- run: `esmr_00e1d4e0c62f4138b983a97913ceae09`
- candidate semantic run: `escr_693fe8c090c14b5dafc9e59159f68ec7`
- comparison: `esrc_f2e0a710eeb8443a881dd460b8478d01`
- result: `COMPLETED / DRIFTED`
- alert: `esma_1b38d988f5954e46ab23953e36a3ff1b`, `CRITICAL`, then
  `ACKNOWLEDGED`
- reasons: `candidate_not_clustered`,
  `pairwise_assignment_agreement_below_minimum`,
  `cluster_count_change_exceeded`, `eligible_cluster_ratio_drop_exceeded`

Both validation monitors were paused after completion so they cannot schedule
additional provider work while remaining available for manual inspection.

## Audit, Outbox, and privacy evidence

The live dispatcher published two created events, two queued events, two
completed events, one alert-opened event, and one alert-acknowledged event. A
second acknowledgement returned HTTP `409`, proving one-time semantics. The
acknowledgement note marker occurred `0` times in both Outbox payloads and Audit
details. Information-schema inspection found no content, vector, or embedding
payload column in any NEXT-017 table.

## Browser and local deployment evidence

The full local development stack was rebuilt and left running. A real Chrome
session logged in through the application, opened `/eval-hub`, selected
`Trace2Dataset`, and rendered:

- both named validation monitors in `PAUSED` state;
- `COMPLETED · PASS` and `COMPLETED · DRIFTED` histories;
- the `CRITICAL / ACKNOWLEDGED` alert and acknowledgement note.

No DuckDock-origin console error occurred; the only captured errors came from a
browser extension. The exact temporary validation user, Namespace membership,
and its two login Audit rows were deleted after the browser check. The paused
monitor/run/alert evidence remains available in the development database.
