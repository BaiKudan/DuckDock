# Eval Hub / Langfuse v4 local evidence — 2026-07-31

## Runtime

- Langfuse Web/Worker: `4.1.0`
- ClickHouse: `25.12.11.4`
- Langfuse health: `{"status":"OK","version":"4.1.0"}`
- Langfuse Web/Worker Compose health: `healthy` / `healthy`
- DuckDock migration head: `20260731_0040`

The disposable Langfuse v3 test database, ClickHouse volumes and `langfuse`
MinIO bucket were cleared before the fresh v4 start. DuckDock MySQL and all
non-Langfuse MinIO data were left intact.

## Real integration checks

1. `backend/scripts/verify_langfuse.py`
   - Python SDK v4 authentication succeeded.
   - Clinic parent span and nested generation flushed.
   - Observations API v2 returned the exact
     `clinic_evaluation_id`.
   - `get_trace_url` returned the v4 trace URL.
2. OpenClaw shadow Collector
   - Authenticated OTLP HTTP returned success.
   - OpenClaw, OpenInference and OTel GenAI fixture traces each appeared
     immediately through Observations API v2.
   - All three normalized to `openclaw.agent.operation` / `AGENT`.
   - `SHADOW_SECRET_CANARY_DO_NOT_EXPORT` was absent from every returned row.
   - Provider metadata contained Collector-injected Namespace, Runtime,
     credential subject, trust and content-capture attributes.
3. Eval Hub provider synchronization
   - DuckDock record: `eds_b1a2ecf548094cb5824ce93dfddbbc60`.
   - Langfuse dataset: `cms8a7yxj0006pd07xe5s18s6`.
   - Provider metadata declares Namespace `1` and
     `duckdock.content_policy=provider-hosted`.
   - No item content was copied into MySQL.

## Automated checks

- Evaluation/Langfuse/architecture regression: `53 passed`.
- Evaluation/Langfuse/Clinic focused suite: `13 passed`.
- Full backend regression: `909 passed, 19 skipped`.
- Ruff: clean.
- Compile/import/OpenAPI: clean.
- Real MySQL:
  - upgrade `0039 -> 0040`: pass;
  - downgrade `0040 -> 0039`: pass;
  - re-upgrade to `0040`: pass;
  - `alembic check`: no new upgrade operations.
- Compose rendering: clean.

## Remaining work

- Evaluation worker and Langfuse Experiment runner: completed by
  [EH-02](eval-hub-eh02-execution-20260731.md).
- Governed case-result artifact references and partial-result semantics.
- Baseline/regression comparison.
- Release Candidate binding and Release Gate enforcement.
- Eval Hub UI.
