# OpenClaw Shadow Telemetry Collector

This directory is the S3/NEXT-001 and NEXT-002 reference trust-boundary
pipeline. It is
intentionally an **edge Collector per registered Runtime**, not a central
multi-tenant router:

1. OTLP/HTTP and OTLP/gRPC require Basic Auth.
2. The authenticated username is retained only as an opaque credential subject.
3. Client `duckdock.*` governance claims are removed before normalization.
4. Collector contrib 0.157.0 `gen_ai_normalizer` maps versioned OpenClaw and
   built-in OpenInference fields to OTel GenAI schema 1.40.0; direct OTel GenAI
   metadata follows the same allowlist.
5. OpenClaw correlation fields are copied only when they match bounded syntax.
   OpenInference and direct OTel GenAI golden inputs must produce the same safe
   GenAI summary.
6. Span events are dropped, spans carrying unreviewed links are rejected,
   span/resource/scope attributes use a fail-closed metadata allowlist, and
   span name/status/tracestate are sanitized.
7. Namespace, Runtime and trust attributes are injected from deployment
   configuration after redaction.
8. The base profile uses `debug`; `langfuse-v4-overlay.yaml` replaces it with
   authenticated OTLP/HTTP export and a bounded `file_storage` WAL queue.

This profile establishes `CHANNEL_AUTHENTICATED + COLLECTOR`; Basic Auth does
not establish producer attestation.

The upstream GenAI normalizer is alpha in Collector 0.157.0, so DuckDock pins
both the image and the exact semantic-convention target in
`duckdock.normalizer.version`. Content-bearing GenAI/OpenInference attributes
are deliberately removed after normalization.

## Development

The optional Compose profile uses explicit development-only defaults and binds
OTLP/health ports to localhost:

```bash
docker compose --profile telemetry up -d otel-collector
curl -fsS http://127.0.0.1:13133/
curl -fsS \
  -u duckdock-runtime-dev:duckdock-otel-dev \
  -H 'Content-Type: application/json' \
  --data-binary @ops/otel-collector/fixtures/openclaw-shadow-trace.json \
  http://127.0.0.1:4318/v1/traces
docker compose logs --no-color otel-collector
```

The logs must contain `run-shadow-001`, the configured trusted Namespace and
Runtime, and `duckdock-genai-shadow-v1+otel-semconv-1.40.0`. They must not contain
`SHADOW_SECRET_CANARY_DO_NOT_EXPORT`, `forged-namespace`,
`forged-runtime`, `forged-client-model`, or the original span name.

Run the full OpenInference/OTel GenAI equivalence and privacy check:

```bash
ops/otel-collector/smoke.sh
```

Validate configuration syntax against the pinned Collector binary:

```bash
docker run --rm \
  -e DUCKDOCK_OTEL_HTPASSWD='runtime:password' \
  -e DUCKDOCK_OTEL_NAMESPACE_PUBLIC_ID='ns_validate' \
  -e DUCKDOCK_OTEL_RUNTIME_PUBLIC_ID='rt_validate' \
  -v "$PWD/ops/otel-collector/openclaw-shadow.yaml:/etc/otelcol/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.157.0 \
  validate --config=/etc/otelcol/config.yaml
```

## Optional Langfuse v4 Export

The overlay accepts the OTLP base endpoint (Collector appends `/v1/traces`),
uses Langfuse project keys as Basic Auth, and sends
`x-langfuse-ingestion-version: 4`:

```bash
DUCKDOCK_LANGFUSE_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel \
DUCKDOCK_LANGFUSE_PUBLIC_KEY=pk-lf-... \
DUCKDOCK_LANGFUSE_SECRET_KEY=sk-lf-... \
docker compose --profile telemetry-langfuse up -d otel-collector-langfuse
```

The local conformance smoke uses a one-shot internal mock and no real provider
data:

```bash
ops/otel-collector/langfuse-smoke.sh
```

It proves the exact OTLP path, Basic Auth, v4 header, safe metadata presence
and Secret Canary absence. `LangfuseTelemetrySinkPort` separately confirms an
exported trace through the bounded Observations API v2 `core` query; provider
failure bodies and credentials are never persisted.

## Durable Queue and Restart Replay

The Langfuse overlay stores sanitized exporter batches in the named
`otel_langfuse_queue_data` volume. The queue uses `fsync`, one ordered consumer,
bounded request capacity, a bounded bbolt file, blocking overflow admission and
unlimited retry elapsed time. The one-shot root init container grants only the
pinned Collector UID access to that volume.

Internal queue metrics are bound to localhost on port `18888` by default:

```bash
curl -fsS http://127.0.0.1:18888/metrics |
  grep otelcol_exporter_queue
```

Run the crash/restart conformance smoke:

```bash
ops/otel-collector/durable-replay-smoke.sh
```

It starts the Collector while Langfuse is unavailable, submits one authenticated
fixture, waits until `otelcol_exporter_queue_size` proves the batch is pending,
proves a non-empty WAL exists, checks transport credentials are absent from the
WAL, kills the Collector without a graceful shutdown, and then proves the
restarted process exports the stored batch without another producer request.

Collector queue acknowledgement and DuckDock trace confirmation are distinct:
removal from the WAL means the OTLP endpoint accepted the batch;
`TraceBackendRef.CONFIRMED` requires the separate bounded provider query. The
pipeline is at-least-once and does not claim exactly-once transport.

Queue/storage admission failure is never described as successful delivery.
Collector exposes exporter queue capacity, size, enqueue-failure and
send-failure metrics; alert before the configured hard limit. Disk exhaustion,
corruption recovery or operator volume removal is declared transport loss and
must downgrade correlated evidence. Sampling is a separate versioned policy,
not transport loss.

## Generic OTLP Bridge

`generic-otlp-bridge.yaml` is the RT-06 reference for Harnesses that can emit
OTLP but cannot call the Reporter Run API. One bridge instance is registered to
one Runtime. It authenticates OTLP/HTTP and OTLP/gRPC, preserves only validated
external Run correlation, deletes every client governance claim, applies the
metadata-only allowlist, injects deployment-owned identity and independently
queues two exports:

- sanitized OTLP spans to the configured trace provider;
- the same sanitized OTLP JSON to
  `/api/v2/reporter/telemetry-sinks/{sink}/v1/traces`.

The second endpoint derives Namespace/Runtime from the `execution.write`
ReporterCredential. It stores only `GenericTraceProjection`, `AgentRun` and
`TraceBackendRef` summaries. One valid root can create a stable
`CHANNEL_AUTHENTICATED + COLLECTOR` Run; no root remains `UNMATCHED`; multiple
roots, conflicting Run signals or reused trace IDs become `QUARANTINED`.
Duplicate and late batches update the projection/reference only and cannot
reopen a terminal Run.

Run the Collector and DD-C2 trust-boundary smoke without a real provider:

```bash
ops/otel-collector/generic-otlp-smoke.sh
```

It proves receiver 401/200 behavior, both authenticated export paths, trusted
identity replacement, stable external Run correlation, metadata-only routing,
and Secret Canary absence from both downstream payloads and Collector logs.
Backend conformance tests cover canonical mapping, quarantine, replay, late
spans, provider-outage isolation and OTLP partial-success responses.

To run the optional Compose profile against real development registrations,
set every `DUCKDOCK_GENERIC_*` value in `.env`, then:

```bash
docker compose --profile telemetry-generic up -d \
  otel-generic-queue-init otel-collector-generic
curl -fsS http://127.0.0.1:14135/
```

## Production Boundary

Do not reuse any development credential. Generate a strong secret or bcrypt
htpasswd entry, store it in the deployment secret manager, map exactly one
active Runtime/Namespace per edge Collector, terminate TLS or mTLS at the
approved boundary, and use the provider overlay instead of the `debug`
exporter before production use.

Place both the Runtime MCP SQLite buffer and Collector named volume on storage
that satisfies the deployment encryption and backup policy. Never copy their
contents into support tickets; use only buffer summaries and stable loss/error
codes.

The reference remains one registered Runtime per Collector. A central
multi-Runtime deployment still requires a reviewed authenticator-to-runtime
lookup implementation; client resource attributes cannot provide that lookup.
Changing identity mapping, normalizer, redaction, buffering or exporter
configuration requires rerunning the conformance fixtures.
