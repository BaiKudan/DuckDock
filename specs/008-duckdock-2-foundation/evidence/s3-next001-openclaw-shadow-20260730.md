# S3 / NEXT-001 OpenClaw Shadow Telemetry Evidence

**Recorded**: 2026-07-30 (Asia/Shanghai)
**Collector**: `otel/opentelemetry-collector-contrib:0.157.0`
**Image digest**:
`sha256:f2f01157055a9b2aab9df7118e1f1c9abf345e99b23bc7a2bc791db374a7d0f6`
**Result**: PASS

> This is the immutable NEXT-001 snapshot. The same base configuration was
> subsequently extended by NEXT-002; see
> `s3-next002-genai-langfuse-20260730.md` for the current normalization and
> provider-export contract.

## Scope

This slice provides an executable reference trust-boundary pipeline for one
registered OpenClaw Runtime per edge Collector. It covers NEXT-001
authentication, trusted mapping, metadata-only redaction and minimal
normalization. It does not claim a Langfuse adapter, DuckDock control-plane
projection, durable replay or full DD-C1/DD-C2 certification.

## Security and Mapping Results

| Check | Result |
|---|---|
| OTLP/HTTP without credentials | `401` |
| OTLP/HTTP with the configured development credential | `200` |
| Client `duckdock.*`, `gen_ai.*`, `openinference.*` claims | removed before normalization |
| Trusted Namespace/Runtime | deployment values injected with `upsert` after redaction |
| Trust | `CHANNEL_AUTHENTICATED + COLLECTOR` |
| OpenClaw Run/Session correlation | bounded IDs normalized |
| Span events | dropped |
| Spans with unreviewed links | dropped |
| Resource/scope schema URL | cleared |
| Scope/span name, status message and tracestate | normalized/cleared |
| Secret Canary and forged identity in exported output | absent |
| Raw telemetry in DuckDock MySQL | none |

The test fixture places `SHADOW_SECRET_CANARY_DO_NOT_EXPORT` in resource,
scope, schema URL, span name, span attributes, event name/body and status
message surfaces. It also sends forged Namespace/Runtime and a forged
pre-normalized model. The detailed debug output contains only the trusted
identity and allowlisted metadata.

## Reproducible Verification

```text
otelcol-contrib validate                         PASS
backend full suite                              834 passed, 16 skipped
backend collector/prod-config focused tests     29 passed
unauthenticated OTLP                            401
authenticated OTLP                              200
secret_canary_absent                            true
trusted_identity_injected                       true
default Compose services                        unchanged (7)
telemetry profile services                      default 7 + otel-collector
```

Commands and fixture:

- `ops/otel-collector/smoke.sh`
- `ops/otel-collector/openclaw-shadow.yaml`
- `ops/otel-collector/fixtures/openclaw-shadow-trace.json`
- `backend/tests/test_otel_collector_reference.py`

## Operational Boundary

The development profile binds OTLP and health ports to localhost and uses an
explicit development-only credential. Shared or production use must supply a
strong secret/bcrypt htpasswd entry, TLS or mTLS at the approved boundary and
must replace the debug exporter.

NEXT-002 owns versioned OpenInference/OTel GenAI normalization, the optional
provider exporter and asynchronous `TraceBackendRef` confirmation. A central
multi-Runtime Collector additionally requires reviewed credential-to-Runtime
lookup and mapping-conflict quarantine; this edge reference must not be
presented as that capability.
