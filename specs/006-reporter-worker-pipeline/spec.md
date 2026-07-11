# 006 Reporter Credential + Worker-First Pipeline

## Decision

DuckDock is the enterprise control plane. Employee-side agents such as Hermes,
OpenClaw, or WorkBuddy are runtime endpoints. The runtime endpoint should enroll
once, keep a long-lived but revocable Reporter credential, upload report packs
through short-lived object-storage URLs, and remain visible through heartbeat.

Report uploads must enter the analysis-worker pipeline as the production path.
Direct report-pack normalization may remain as an administrator-only compatibility
or debugging tool, but it is not the product path for enterprise asset governance.

## Target Flow

```text
employee/admin invites or employee self-enrolls
  -> DuckDock creates/reuses a runtime endpoint
  -> DuckDock issues a long-lived dkr_report_* credential

agent-side Reporter
  -> heartbeat with version/schedule/capability status
  -> build duckdock-pack-v1.zip
  -> POST /reports/upload-sessions with dkr_report_* credential
  -> PUT zip to short-lived presigned MinIO/S3 URL
  -> POST /reports/{report_id}/finalize

DuckDock
  -> validates size and sha256
  -> creates ReportAnalysisJob
  -> analysis workers lease jobs from the queue
  -> workers download pack through one-time GET URL
  -> workers run versioned recipe/prompt/model/baseline
  -> workers upload standard result files
  -> finalize validates result artifact hashes
  -> materializer writes canonical MySQL rows
  -> admin console shows runtime health, worker result, assets, timeline, evidence
```

## Non-Goals

- Do not give reporters MinIO/S3 access keys.
- Do not monitor local user machines continuously.
- Do not use MinIO object events as the source of truth for job orchestration.
- Do not let raw chat transcripts, auth files, or secret fingerprints enter
  MySQL-facing payloads.

## Design Constraints

- Long-lived Reporter credentials must be revocable and rotatable.
- Each upload still uses a short-lived presigned PUT URL.
- Reporter heartbeat is distinct from report upload; an agent can be online even
  when it has not uploaded a new pack yet.
- Workers must be horizontally scalable. Multiple workers can lease from the
  same job pool.
- Worker output is schema-first. DuckDock materializes only validated structured
  results into relational tables.
- Every materialized result should remain traceable to runtime, report id,
  worker id, result object key, sha256, recipe/prompt/model metadata when
  available.

## Current Increment

This first increment deliberately reuses existing tables where possible:

- `RuntimeInstance` remains the runtime endpoint.
- `ReporterCredential` is the long-lived, self-service credential carrier.
- `RuntimeReportToken` remains a legacy/admin compatibility path for existing
  upload clients.
- Runtime `metadata_json.reporter` stores latest self-service enrollment and
  heartbeat status; `reporter_credentials` stores token lifecycle state.
- `finalize_report_upload_session` always creates a `ReportAnalysisJob`.
- `/reports/{report_id}/ingest` remains admin-only for compatibility/debugging.
- Analysis job finalize validates `duckdock-analysis-v1` result artifacts before
  materialization and stores worker recipe/prompt/model/trace metadata in
  `ReportAnalysisJob.summary_json`.
- `materialized_counts` preserves legacy inserted counters and also exposes
  `parsed_counts`, `inserted_counts`, `updated_counts`, `deduped_counts`, and
  `skipped_counts` for admin review.
- Queue orchestration remains DuckDock's MySQL lease protocol plus Celery beat
  reaper. `/analysis/queue/metrics` exposes backlog, online worker, expired
  lease, oldest pending, and saturation signals. Temporal/Prefect is deferred
  until the analysis flow needs durable multi-step workflow recovery.

## Acceptance

- A normal authenticated user can self-enroll a Reporter endpoint and receive a
  one-time visible `dkr_report_*` token.
- A Reporter credential can heartbeat without an admin JWT.
- Heartbeat updates runtime metadata and token last-used status.
- Finalizing an upload creates or reuses a pending analysis job and does not
  enqueue direct report-pack ingestion.
- Invalid worker result schemas fail the analysis job before materialization.
- The analysis console shows result file count, schema version, worker
  model/trace metadata, and parsed/inserted/updated/deduped materialization
  totals.
- The analysis console shows queue saturation metrics that are computed server
  side across all jobs, not inferred from the first page of task rows.
- Existing administrator-created report tokens continue to upload packs.
- Existing analysis-worker lease/finalize protocol remains compatible.
