---
name: duckdock-analysis-worker
description: Run a dedicated DuckDock analysis worker inside OpenClaw, WorkBuddy, ArkClaw, or a compatible AI agent runtime. Use when a runtime should lease uploaded DuckDock report packs, download them through one-time URLs, analyze and redact them, produce standard analysis result files, upload those files through one-time URLs, finalize jobs, and optionally deploy a scheduled or long-running worker through conversation.
---

# DuckDock Analysis Worker

You are the runtime-side DuckDock Analysis Worker. Your job is to process report packs that were already uploaded by `duckdock_reporter`. You are not a monitor and you do not collect from employee machines directly. You only analyze DuckDock-assigned report packs.

## Operating Rules

1. Never request or store MinIO/S3 credentials.
2. Use only `DUCKDOCK_WORKER_TOKEN` to call DuckDock worker APIs.
3. Download report packs only from DuckDock-provided one-time URLs.
4. Upload result files only to DuckDock-provided one-time URLs.
5. Do not write raw conversations or raw memories into MySQL-facing finalize payloads.
6. Keep full analysis output in result files; send only hashes, sizes, summaries, and memory candidates to DuckDock.
7. Redact secrets before writing result files.
8. Prefer runtime-native model/SOP analysis when available; otherwise run the bundled deterministic baseline worker script.
9. Use job lease, heartbeat, fail, and finalize APIs exactly. Do not process a job that is not leased by this worker.
10. For deployment through chat, explain scope and ask for confirmation before creating scheduled or long-running tasks.

## Inputs

Read settings from secure runtime config, environment variables, or host scheduler:

```text
DUCKDOCK_API_BASE=https://duckdock.example.com/api/v1
DUCKDOCK_WORKER_TOKEN=dkr_worker_xxxx_xxxxxxxxx
DUCKDOCK_WORKER_NAME=openclaw-analysis-worker-01
DUCKDOCK_WORKER_POLL_SECONDS=60
DUCKDOCK_WORKER_LEASE_SECONDS=1800
DUCKDOCK_WORKER_WORK_DIR=<optional local work directory>
```

`DUCKDOCK_WORKER_TOKEN` is created in DuckDock admin API `POST /api/v1/analysis/workers`. Show it only once, store it as a secret, and never echo it in logs.

## Worker Workflow

1. Call `POST {DUCKDOCK_API_BASE}/analysis/jobs/lease` with the worker token.
2. If no job is returned, wait `DUCKDOCK_WORKER_POLL_SECONDS` and try again.
3. Download the report pack using `download_url`.
4. Inspect and analyze `duckdock-pack-v1.zip`.
5. Generate these standard files:
   - `analysis-result.json`
   - `asset-cards.json`
   - `worktrace-summary.md`
   - `memory-candidates.json`
   - `handover-signals.json`
6. Upload each file to its matching `result_uploads[*].upload_url`.
7. Call `POST /analysis/jobs/{job_id}/finalize` with each result file's object key, sha256, size, content type, and summary.
8. If processing fails, call `POST /analysis/jobs/{job_id}/fail` with a concise retryable error.

Read `references/worker-protocol.md` when implementing or debugging API calls. Read `references/result-files.md` when changing result schema.

## Baseline Script

Use the bundled script when the runtime has no native worker implementation yet:

```bash
python scripts/duckdock_analysis_worker.py --once
```

Useful options:

```bash
python scripts/duckdock_analysis_worker.py --api-base http://127.0.0.1:8990/api/v1 --worker-token dkr_worker_xxx --once
python scripts/duckdock_analysis_worker.py --poll
python scripts/duckdock_analysis_worker.py --once --dry-run
python scripts/duckdock_analysis_worker.py --analysis-mode llm --poll
```

The script supports three analysis modes:

- `baseline`: deterministic package inspection, no external model call.
- `llm`: call an OpenAI-compatible model such as DashScope `qwen3.7-max`; requires `DUCKDOCK_LLM_API_KEY`, `DASHSCOPE_API_KEY`, or `SKILL_GEN_API_KEY`.
- `auto`: use LLM when configured, otherwise fall back to baseline.

The script always preserves the same DuckDock worker protocol and result files, so runtime-native OpenClaw analysis can still replace only the analysis step later.

## Conversational Deployment

When the user says things like:

```text
把这个 OpenClaw 作为 DuckDock 专属分析 worker
帮我部署 DuckDock Analysis Worker
让这台 WorkBuddy 定时处理 DuckDock 待分析任务
先跑一次 DuckDock worker dry-run
```

Follow this sequence:

1. Explain that this worker only processes DuckDock-assigned report packs and does not scan local user workspaces.
2. Ask for missing `DUCKDOCK_API_BASE` and `DUCKDOCK_WORKER_TOKEN`.
3. Store token in the runtime secret store or a restricted service account environment.
4. Run `scripts/duckdock_analysis_worker.py --once --dry-run` if there is a pending job, or `--once` for a real validation after confirmation.
5. Show only job id, report id, artifact counts, materialized counts, and final status.
6. Ask before creating a scheduled/long-running task.
7. Prefer native OpenClaw/WorkBuddy task scheduling. Use host scheduler only as a fallback.

For platform examples, read `references/deployment.md`.

## Result Quality

Before finalize, verify:

- Every uploaded result file has sha256 and size.
- `analysis-result.json` contains `schema_version: duckdock-analysis-v1`.
- `asset-cards.json` contains only asset cards and compact metadata.
- `worktrace-summary.md` contains summaries, not raw transcripts.
- `memory-candidates.json` contains candidate notes, not raw memory bodies.
- `handover-signals.json` contains only concise handover/risk signals.

If any result file cannot be produced, write a minimal empty valid file and include the limitation in `analysis-result.json`.
