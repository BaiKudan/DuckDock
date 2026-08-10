# S3 / RT-05 Durable Buffer and Offline Replay Evidence

**Recorded**: 2026-07-30 (Asia/Shanghai)
**Runtime MCP**: `0.3.0`
**Collector**: `otel/opentelemetry-collector-contrib:0.157.0`
**Result**: PASS

## Scope

This slice closes the OpenClaw RT-05 control-envelope and telemetry transport
durability boundary:

- Runtime MCP persists each metadata-only Session/Run envelope in SQLite WAL
  before its first network attempt.
- Queue sequence, canonical envelope hash and idempotency key are stable across
  process restart.
- Offline dependent operations reference an earlier local sequence. Replay
  durably stores the server acknowledgement, resolves the returned Session/Run
  public ID, sends the dependent operation and only then advances the ack
  cursor.
- The current Reporter credential is loaded at send time. Queue rows contain
  the originating Runtime ID but no bearer credential, and a credential for a
  different Runtime cannot replay them.
- Item and byte limits are bounded. The adapter reports pending age/count/bytes,
  retry count, last safe error, pressure and the ack cursor. It never evicts
  automatically.
- Explicit operator discard requires an exact range, safe reason and
  `confirm=true`; payloads are blanked while a durable range/count/reason
  marker retains `completeness=PARTIAL` and `trust_state=DECLARED_LOSS`.
- The Langfuse OTLP exporter uses Collector `file_storage` with `fsync`, bounded
  bbolt size/queue capacity, blocking overflow admission and indefinite
  retryable backoff.

These are two separately acknowledged queues. Runtime MCP ack means the
DuckDock lifecycle API stored/replayed the domain fact. Collector WAL removal
means the provider OTLP endpoint accepted a sanitized batch.
`TraceBackendRef.CONFIRMED` still requires the separate provider query.

## Conformance Results

| Check | Result |
|---|---|
| `CONF-C3-001` crash-safe local persistence | SQLite WAL and Collector file-storage WAL verified |
| `CONF-C3-002` durable ack cursor | ordered cursor advances only after stored ACK/rejection/discard |
| Offline dependency ordering | Session start → Run start → Run complete → Session complete |
| `CONF-C3-003` recovery | rotated same-Runtime credential and recovered provider replay pending work |
| Runtime isolation | different configured Runtime stops with `RUNTIME_CREDENTIAL_MISMATCH` |
| Credential persistence | Reporter/OTLP credentials absent from queue rows and Collector WAL |
| `CONF-C3-004` bounded pressure | item/byte high-water and hard-limit fail-closed behavior verified |
| `CONF-C3-005` explicit loss | durable range/count/reason + `PARTIAL` / `DECLARED_LOSS` marker |
| Collector crash | process killed with `SIGKILL` while provider unavailable |
| Restart replay | stored trace exported after restart with no second producer request |
| Privacy after replay | Secret Canary and forged governance absent at mock provider |
| Delivery claim | at-least-once; no end-to-end exactly-once claim |

## Reproducible Verification

```text
backend full suite                               845 passed, 16 skipped
Runtime MCP suite                               12 passed
Collector + Runtime MCP focused suite           18 passed
Collector merged overlay validation             PASS
Langfuse protocol live smoke                    PASS
pending queue metric observed                   true
Collector WAL observed before crash             true
Collector SIGKILL/restart replay                 true
second producer request                          false
transport credentials absent from WAL           true
Secret Canary absent after replay               true
default Compose services                         unchanged (7)
```

Commands and implementation:

- `ops/otel-collector/durable-replay-smoke.sh`
- `ops/otel-collector/langfuse-smoke.sh`
- `ops/otel-collector/langfuse-v4-overlay.yaml`
- `tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py`
- `tools/duckdock-runtime-mcp/tests/test_duckdock_runtime_mcp.py`
- `backend/tests/test_otel_collector_reference.py`
- `docs/operations-runbook.zh-CN.md`

## Loss and Operations Boundary

Queue-full admission does not silently acknowledge new data: Runtime MCP
returns a hard-limit error and Collector blocks/rejects admission while
recording enqueue-failure metrics. Existing unacknowledged items remain
durable.

The Collector does not enable automatic corrupted-database recreation. Disk
failure, manual volume removal or operator recovery is an incident, not normal
sampling. Operators must record the affected trace/range as transport loss and
downgrade evidence completeness before recovering the queue. The local volume
and Runtime MCP directory must use deployment-approved disk encryption and
backup controls.

The provider live test remains an internal protocol mock, not a real Langfuse
tenant. RT-06 still owns Generic multi-Runtime OTLP routing, ambiguity
quarantine and shared conformance. RT-07 still owns Pack/ATIF durability.
