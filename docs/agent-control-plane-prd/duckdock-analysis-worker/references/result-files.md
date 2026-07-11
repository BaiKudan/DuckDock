# Result Files

The worker must produce a standard result file set. These files are uploaded to MinIO through DuckDock-provided one-time URLs. DuckDock reads them and saves only simplified indexes in MySQL.

## `analysis-result.json`

```json
{
  "schema_version": "duckdock-analysis-v1",
  "report_id": "rpt_xxx",
  "summary": {
    "asset_count": 3,
    "worktrace_count": 8,
    "memory_candidate_count": 2,
    "handover_signal_count": 1,
    "risk_count": 1,
    "analysis_mode": "llm-worker",
    "ai_assist": {
      "mode": "llm",
      "degraded": false,
      "reason": null
    }
  },
  "processing": {
    "mode": "llm-worker",
    "redaction": "completed",
    "classification": "llm",
    "recipe_version": "duckdock-analysis-worker-v1",
    "prompt_version": "duckdock-analysis-prompt-v1",
    "model": "qwen3.7-max"
  },
  "worker_metadata": {
    "recipe_version": "duckdock-analysis-worker-v1",
    "prompt_version": "duckdock-analysis-prompt-v1",
    "model": "qwen3.7-max",
    "trace_id": "provider-request-or-langfuse-trace-id",
    "token_usage": {
      "prompt_tokens": 1200,
      "completion_tokens": 400,
      "total_tokens": 1600
    },
    "analysis_mode": "llm-worker"
  },
  "limitations": []
}
```

DuckDock validates this file during analysis-job finalize. `schema_version`, matching `report_id`, and object-shaped `summary` are required. `worker_metadata` is copied into `ReportAnalysisJob.summary_json` so the admin console can show which recipe/prompt/model produced the indexed result.

## `asset-cards.json`

Array of compact asset cards.

```json
[
  {
    "external_id": "skill/example",
    "asset_type": "skill",
    "name": "Example Skill",
    "description": "Reusable business workflow.",
    "criticality": "medium",
    "status": "active",
    "project": "project-a",
    "owner_hint": "employee",
    "confidence": 0.86
  }
]
```

Valid `asset_type` values:

```text
skill, agent, prompt, workflow, mcp, tool, knowledge_base, scheduled_task, credential_ref, workspace, other
```

## `worktrace-summary.md`

Human-readable summary only. Do not include raw transcripts.

```markdown
# Weekly work summary

- Reviewed customer deployment workflow.
- Updated one reusable automation.
- Handover notes are summarized in handover-signals.json.
```

## `memory-candidates.json`

Array of compact memory candidates.

```json
[
  {
    "candidate_type": "project_context",
    "subject_type": "project",
    "subject_key": "project-a",
    "title": "Project A deployment context",
    "summary": "Project A uses private OpenClaw and weekly DuckDock reporting.",
    "confidence": 0.82,
    "sensitivity": "internal"
  }
]
```

Valid `candidate_type` values:

```text
asset_summary, worktrace_summary, project_context, ownership_signal, handover_signal, risk_signal, knowledge_note
```

## `handover-signals.json`

Array of handover and risk signals.

```json
[
  {
    "signal_type": "handover",
    "subject_type": "skill",
    "subject_key": "skill/example",
    "title": "Example Skill needs owner confirmation",
    "summary": "Confirm maintainer before employee offboarding.",
    "confidence": 0.78,
    "sensitivity": "internal"
  },
  {
    "signal_type": "risk",
    "subject_type": "credential_ref",
    "subject_key": "vault://project-a/openclaw",
    "title": "Credential reference needs rotation review",
    "summary": "Only the credential reference is recorded. No secret value is included.",
    "confidence": 0.7,
    "sensitivity": "confidential"
  }
]
```

## Redaction

Result files may contain compact summaries, paths, object references, hashes, and risk descriptions. They must not contain:

- tokens, API keys, cookies, session IDs;
- private keys or credential values;
- raw personal chats;
- raw memory bodies;
- unrelated local files or OS inventory.
