# S3 / NEXT-002 GenAI Normalization and Langfuse Adapter Evidence

**Recorded**: 2026-07-30 (Asia/Shanghai)
**Collector**: `otel/opentelemetry-collector-contrib:0.157.0`
**Target schema**: `https://opentelemetry.io/schemas/1.40.0`
**Result**: PASS

## Scope

This slice closes RT-02, RT-03 and RT-04:

- Runtime MCP exposes explicit metadata-only Session/Run start and complete
  operations. The Runtime supplies stable external Session/Run IDs and the same
  OTel trace/root-span IDs used by its root span.
- The pinned Collector uses the upstream `gen_ai_normalizer` for built-in
  OpenInference plus a versioned OpenClaw mapping. Direct OTel GenAI metadata
  follows the same fail-closed allowlist.
- A Langfuse v4 overlay replaces the debug exporter with OTLP/HTTP Basic Auth,
  the v4 ingestion header, retry and an in-memory sending queue.
- `LangfuseTelemetrySinkPort` performs a bounded Observations API v2 `core`
  lookup to confirm `TraceBackendRef`; provider failures remain outside the
  committed AgentRun transaction.

The upstream GenAI normalizer is alpha in Collector 0.157.0. DuckDock therefore
pins the image, mapping configuration, OTel schema target and
`duckdock.normalizer.version`.

## Correlation and Privacy Results

| Check | Result |
|---|---|
| Runtime MCP lifecycle tools | Session start/complete and Run start/complete exposed |
| Retry identity | deterministic safe idempotency key; caller event timestamps required |
| Governance identity | Namespace/Runtime/Trust never accepted in lifecycle payloads |
| OpenClaw correlation | external Session/Run + OTel trace/root span preserved together |
| OpenInference normalization | model, provider, token counts, operation and conversation normalized |
| Direct OTel GenAI | same six-field canonical summary as the OpenInference golden span |
| Content surfaces | input/output messages, tool arguments/definitions, events and status message removed |
| Secret Canary / forged governance | absent after debug and Langfuse exporter paths |
| Langfuse endpoint | `/api/public/otel/v1/traces` |
| Langfuse authentication/header | Basic Auth + `x-langfuse-ingestion-version: 4` |
| Confirmation lookup | bounded `/api/public/v2/observations?fields=core&traceId=...` |
| Provider failures | safe codes only; response bodies/credentials not persisted |
| Raw telemetry in DuckDock MySQL | none |

## Reproducible Verification

```text
backend full suite                              845 passed, 16 skipped
Runtime MCP lifecycle suite                     10 passed
Collector/Langfuse/Foundation focused suite     32 passed
Ruff selected implementation/tests              PASS
Collector base config validate                  PASS
Collector base live smoke                       401 unauth / 200 auth
OpenInference == direct OTel GenAI summary       true
Secret Canary absent                            true
Langfuse overlay config validate                PASS
Langfuse exporter live protocol smoke           PASS
Langfuse Basic Auth / v4 header / safe payload   true / true / true
default Compose services                        unchanged (7)
running development services                    default 7 + otel-collector
```

Commands and implementation:

- `ops/otel-collector/smoke.sh`
- `ops/otel-collector/langfuse-smoke.sh`
- `ops/otel-collector/openclaw-shadow.yaml`
- `ops/otel-collector/langfuse-v4-overlay.yaml`
- `ops/otel-collector/verify_conformance.py`
- `backend/app/services/telemetry_sinks/langfuse.py`
- `tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py`
- `backend/tests/test_otel_collector_reference.py`
- `backend/tests/test_langfuse_telemetry_sink.py`
- `tools/duckdock-runtime-mcp/tests/test_duckdock_runtime_mcp.py`

## Boundaries and Remaining Work

The Langfuse live smoke uses an internal one-shot protocol mock, not a real
Langfuse tenant, so it proves the outbound contract without writing provider
test data. The confirmation adapter uses mocked provider responses for success
and failure mapping.

The current sending queue is intentionally in-memory. RT-05 owns persistent
queue storage, restart replay, acknowledgement/loss semantics and offline
conformance. Generic multi-Runtime OTLP routing and mapping-conflict quarantine
remain RT-06; Pack/ATIF remains RT-07. This evidence does not claim DD-C3,
Generic OTLP, Fleet or G2 completion.
