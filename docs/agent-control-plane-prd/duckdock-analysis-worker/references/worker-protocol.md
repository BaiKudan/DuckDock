# Worker Protocol

## Authentication

Use:

```http
Authorization: Bearer dkr_worker_<prefix>_<secret>
```

The token is created by a DuckDock admin:

```http
POST /api/v1/analysis/workers
```

Never put this token in a report pack, result file, chat transcript, or log.

## Lease

```http
POST /api/v1/analysis/jobs/lease
Content-Type: application/json
```

```json
{
  "lease_seconds": 1800,
  "worker_name": "openclaw-worker-01",
  "capabilities_json": {
    "runtime": "openclaw",
    "skill": "duckdock-analysis-worker"
  }
}
```

If `job` is `null`, there is no pending job.

If a job is returned:

- `download_url` is the one-time GET URL for the uploaded report pack.
- `result_uploads` contains one-time PUT URLs for standard result files.
- `job.job.id` is the analysis job id used for heartbeat/finalize/fail.

## Heartbeat

For long analysis runs:

```http
POST /api/v1/analysis/jobs/{job_id}/heartbeat
```

Heartbeat extends the lease and marks the job running.

## Upload Results

For each `result_uploads` item:

1. Generate the local file.
2. Compute sha256 and size.
3. PUT the file to `upload_url` with the exact `content_type`.

No MinIO key is needed.

## Finalize

```http
POST /api/v1/analysis/jobs/{job_id}/finalize
Content-Type: application/json
```

```json
{
  "summary_json": {
    "asset_count": 12,
    "worktrace_count": 48,
    "risk_count": 2
  },
  "result_artifacts": [
    {
      "kind": "analysis_result",
      "filename": "analysis-result.json",
      "object_key": "analysis/2026/05/21/rpt_xxx/job-1/analysis-result.json",
      "content_type": "application/json",
      "sha256": "64-char-sha256",
      "size_bytes": 1234
    }
  ],
  "memory_candidates": []
}
```

Prefer putting memory candidates in `memory-candidates.json`. Use inline `memory_candidates` only for small compatibility payloads.

## Fail

```http
POST /api/v1/analysis/jobs/{job_id}/fail
Content-Type: application/json
```

```json
{
  "error_message": "Could not parse report pack manifest",
  "retryable": true,
  "summary_json": {
    "stage": "parse"
  }
}
```

Use `retryable=true` for transient network/model/tool failures. Use `retryable=false` for malformed packages or policy-blocked inputs.
