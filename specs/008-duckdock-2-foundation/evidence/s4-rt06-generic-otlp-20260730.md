# S4 / RT-06 Generic OTLP Bridge Evidence

**Date**: 2026-07-30
**Scope**: trusted Generic OTLP bridge, canonical trace/Run reconciliation,
mapping-conflict quarantine, metadata-only dual export and DD-C2 conformance.

## Result

RT-06 is complete for the registered per-Runtime reference profile.

- Collector contrib is pinned to `0.157.0`.
- OTLP/HTTP and OTLP/gRPC require receiver Basic Auth.
- Client `duckdock.*` governance claims are removed; only validated Run
  correlation survives; Runtime/Namespace/trust are deployment-owned.
- Sanitized OTLP is independently and durably queued to the configured trace
  provider and the DuckDock projection endpoint.
- DuckDock authenticates that endpoint with an `execution.write`
  ReporterCredential and derives Runtime/Namespace server-side.
- MySQL stores only `AgentRun`, `TraceBackendRef` and
  `GenericTraceProjection` summaries. It stores no span arrays, events,
  attributes, prompt/tool content or provider bodies.
- One valid root creates an idempotent
  `CHANNEL_AUTHENTICATED + COLLECTOR` terminal Run. No root remains
  `UNMATCHED`. Multiple roots, reused trace IDs or conflicting Run signals
  become `QUARANTINED` and never merge/move Runs.
- Duplicate and late batches update only the projection/reference summary and
  cannot reopen or rewrite a terminal Run.

The reference remains one Runtime per Collector. A central multi-Runtime
authenticator-to-Runtime lookup remains outside this slice; client resource
attributes cannot provide that authority.

## Contract and persistence

- API:
  `POST /api/v2/reporter/telemetry-sinks/{sink_public_id}/v1/traces`
- Wire format: uncompressed OTLP/HTTP JSON, bounded to 1 MiB and 2,000 spans.
- Full success: OTLP JSON `{}` with HTTP 200.
- Partial success: HTTP 200 with `partialSuccess.rejectedSpans` and one bounded
  safe reason; rejected data is not retried as an accepted batch.
- Revision: `20260730_0035`
- New table: `generic_trace_projections`
- Unique trace identity:
  `(namespace_id, telemetry_sink_id, external_trace_id)`
- Statuses: `MAPPED`, `UNMATCHED`, `QUARANTINED`

The implementation follows the official OTLP rules for JSON lower-camel
fields, hexadecimal trace/span IDs, `/v1/traces`, HTTP 200 partial success and
non-retry of partially accepted requests:
<https://opentelemetry.io/docs/specs/otlp/>.
The pinned Collector OTLP/HTTP exporter explicitly uses `encoding: json`,
`compression: none`, a bounded persistent queue and indefinite retry:
<https://github.com/open-telemetry/opentelemetry-collector/blob/v0.157.0/exporter/otlphttpexporter/README.md>.

## Conformance matrix

| Fixture / gate | Evidence |
|---|---|
| OTL-001 / CONF-C2-001 | Single authenticated root creates one terminal Run, pending TraceBackendRef and mapped projection |
| OTL-002 / CONF-C2-002 | Live Collector smoke removes forged Namespace/Runtime/trust and injects registered identity |
| OTL-003 / CONF-C2-003 | Two roots produce `ambiguous_root_spans` quarantine; no Run/reference |
| OTL-004 / CONF-C2-004 | Late child changes observation summary only; terminal Run snapshot remains byte-for-byte stable |
| OTL-005 / CONF-C2-004 | Duplicate export returns the same projection with one Run, one reference and no extra Outbox events |
| OTL-006 / CONF-C2-006 | Prompt/tool attributes are absent from MySQL projection, Outbox and audit bodies |
| OTL-007 / CONF-C2-005 | Provider starts unavailable; DuckDock projection succeeds; provider WAL queue becomes non-zero and later replays without a second producer request |
| OTL-008 / CONF-C2-007 | Invalid span identity returns standard OTLP HTTP 200 partial success with exact rejected count |
| OTL-009 / CONF-C2-008 | Default metadata-only Collector path drops prompt/messages/tool content before both downstream endpoints |
| CONF-C2-009 | Secret Canary and forged markers are absent from both downstream payloads and Collector logs |

Authorized content forwarding remains disabled in this Foundation reference.
OTL-010 therefore does not claim an authorized-content mode; enabling one
requires a versioned Namespace authorization, edge redaction receipt and a
separate review.

## Executed verification

```text
docker Collector validate generic-otlp-bridge.yaml        PASS
ops/otel-collector/generic-otlp-smoke.sh                  PASS
  receiver unauthenticated request                       401
  DuckDock projection export                             PASS
  provider unavailable / durable queue > 0               PASS
  provider recovery without second producer request      PASS
  content/forgery/canary absent                           PASS

revision 0034 -> 0035 on development MySQL               PASS
alembic current                                           20260730_0035 (head)
alembic check                                             clean

focused RT-06 + Collector + migration + contract tests   40 passed
focused artifact/telemetry regression                    28 passed
full backend suite                                       859 passed, 16 skipped
Ruff                                                      clean
compileall                                                clean
mypy                                                      82 <= 82 baseline
default Compose services                                  unchanged (7)
development backend/frontend/OpenClaw Collector health    PASS
```

## Files

- `ops/otel-collector/generic-otlp-bridge.yaml`
- `ops/otel-collector/generic-otlp-smoke.sh`
- `ops/otel-collector/mock_generic_otlp.py`
- `ops/otel-collector/fixtures/generic-otlp-trace.json`
- `backend/app/services/generic_otlp_service.py`
- `backend/app/api/v2/endpoints/generic_otlp.py`
- `backend/app/models/telemetry.py`
- `backend/alembic/versions/20260730_0035_generic_otlp_projection.py`
- `backend/tests/foundation/test_generic_otlp_bridge.py`
- `backend/tests/foundation/test_generic_otlp_projection_migration.py`

## Next

RT-07 owns Pack/ATIF manifest preflight, safe archive handling, MinIO content
lifecycle, resumable import and integrity/replay semantics. RT-08 still owns
three-profile shared conformance aggregation, dynamic capability/Fleet state
and the G2 evidence package.
