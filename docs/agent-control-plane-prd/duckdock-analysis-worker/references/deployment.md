# Deployment

Use this reference when a user asks to deploy a dedicated DuckDock Analysis Worker through conversation.

## Conversation Scope

Start with:

```text
我会把当前 OpenClaw/WorkBuddy 配置成 DuckDock 专属分析 worker。它只处理 DuckDock 已接收的 report pack，不扫描你的本地工作区，也不接触 MinIO 密钥。需要 DuckDock API 地址和一个 Analysis Worker Token。是否继续？
```

Continue only after confirmation.

## Required Settings

Ask only for missing values:

```text
DUCKDOCK_API_BASE
DUCKDOCK_WORKER_TOKEN
```

Optional:

```text
DUCKDOCK_WORKER_NAME
DUCKDOCK_WORKER_POLL_SECONDS
DUCKDOCK_WORKER_LEASE_SECONDS
DUCKDOCK_WORKER_WORK_DIR
```

## Secure Storage

Prefer:

1. runtime secret store;
2. OS user secret store;
3. service account environment variables;
4. restricted config file.

Do not echo the token after storing.

## OpenClaw Pattern

If OpenClaw has native skills/tasks:

```text
Install duckdock-analysis-worker.
Store DUCKDOCK_WORKER_TOKEN as a secret.
Create a task:
  name: DuckDock Analysis Worker
  action: run duckdock-analysis-worker poll
  schedule: always-on or every 1 minute
  retry: enabled
  timeout: 30 minutes per job
```

If only CLI is available:

```bash
export DUCKDOCK_API_BASE=https://duckdock.example.com/api/v1
export DUCKDOCK_WORKER_TOKEN=dkr_worker_xxx
export DUCKDOCK_WORKER_WORK_DIR=/tmp/duckdock-analysis-worker
export DUCKDOCK_ANALYSIS_MODE=baseline
python scripts/duckdock_analysis_worker.py --poll
```

For lightweight model analysis:

```bash
export DUCKDOCK_ANALYSIS_MODE=llm
export DUCKDOCK_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export DUCKDOCK_LLM_MODEL=qwen3.7-max
export DUCKDOCK_LLM_ENABLE_THINKING=true
export DUCKDOCK_LLM_API_KEY=store-this-as-a-secret
python scripts/duckdock_analysis_worker.py --poll
```

## WorkBuddy Windows Pattern

Prefer WorkBuddy automation if available. Fallback to Windows Task Scheduler only after confirmation.

Suggested scheduled task:

```powershell
$env:DUCKDOCK_API_BASE="https://duckdock.example.com/api/v1"
$env:DUCKDOCK_WORKER_TOKEN="dkr_worker_xxx"
$env:DUCKDOCK_WORKER_WORK_DIR="$env:TEMP\duckdock-analysis-worker"
python .\scripts\duckdock_analysis_worker.py --poll
```

Recommended:

```text
Trigger: at startup or every 1 minute
Run as: approved service account/current runtime user
Retry: 3 attempts
Stop if running longer than: 60 minutes
Network required: yes
```

## Validation

First run:

```bash
python scripts/duckdock_analysis_worker.py --once --dry-run
```

Then real test:

```bash
python scripts/duckdock_analysis_worker.py --once
```

Report only:

```text
job_id
report_id
result artifact count
final status
materialized counts
```

Do not display downloaded report content or generated sensitive summaries in chat.
