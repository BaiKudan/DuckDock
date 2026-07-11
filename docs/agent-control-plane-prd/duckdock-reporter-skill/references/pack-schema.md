# DuckDock Pack V1 Schema

Use this reference when building or validating `duckdock-pack-v1.zip`.

## manifest.json

```json
{
  "schema_version": "duckdock-pack-v1",
  "generated_at": "2026-05-22T16:00:00+08:00",
  "runtime_id": "workbuddy-local-001",
  "provider": "workbuddy",
  "report_type": "weekly",
  "period_start": "2026-05-15T00:00:00+08:00",
  "period_end": "2026-05-22T00:00:00+08:00",
  "collector": {
    "name": "duckdock_reporter",
    "version": "0.1.0",
    "mode": "summary_index"
  }
}
```

## runtime.json

```json
{
  "runtime_id": "workbuddy-local-001",
  "provider": "workbuddy",
  "name": "Local WorkBuddy",
  "deploy_type": "desktop",
  "host": {
    "os": "windows",
    "hostname_hash": "sha256-prefix",
    "username_hash": "sha256-prefix"
  },
  "app": {
    "name": "WorkBuddy",
    "version": null,
    "install_root": "%LOCALAPPDATA%\\Tencent\\WorkBuddy",
    "workspace_root": null
  },
  "collection_policy": {
    "include_raw": false,
    "respect_duckdockignore": true,
    "redaction": true
  }
}
```

## principals.ndjson

```json
{"id":"user-hash-001","type":"user","display_name":"User","username":"user","email":null}
```

## skills.ndjson / agents.ndjson / prompts.ndjson / workflows.ndjson / tools.ndjson

```json
{"id":"skill-001","asset_type":"skill","name":"Customer Summary","description":"Summarizes customer context","version":"1.2.0","owner_external_id":"user-hash-001","source_path":"skills/customer-summary/SKILL.md","sha256":"...","last_used_at":"2026-05-21T10:30:00+08:00","criticality":"medium","status":"active"}
```

## memories.ndjson

```json
{"id":"memory-001","asset_type":"knowledge_base","name":"Project A Context","scope":"project","summary":"Project A delivery context and constraints","owner_external_id":"user-hash-001","sha256":"...","updated_at":"2026-05-21T10:30:00+08:00","sensitivity":"internal"}
```

## sessions.ndjson

```json
{"id":"session-001","trace_type":"session","title":"Project A weekly delivery","summary":"Discussed delivery blockers and generated an action list.","asset_id":"skill-001","created_by":"user-hash-001","started_at":"2026-05-21T10:00:00+08:00","ended_at":"2026-05-21T10:30:00+08:00","sensitivity":"internal","artifact_ids":["artifact-001"]}
```

## artifacts.ndjson

```json
{"id":"artifact-001","artifact_type":"report","name":"weekly-summary.md","path":"outputs/weekly-summary.md","sha256":"...","size_bytes":4096,"created_at":"2026-05-21T10:30:00+08:00","sensitivity":"internal","related_session_id":"session-001"}
```

## evidence.ndjson

```json
{"id":"evidence-001","source_type":"backup_package","summary":"Weekly report generated from WorkBuddy session index","object_uri":"duckdock-pack-v1.zip#summaries/weekly-report.md","sha256":"...","confidence":1.0,"visibility":"normal"}
```

## redaction-report.json

```json
{
  "enabled": true,
  "rules_version": "duckdock-redaction-v1",
  "redacted_items": [
    {
      "path": "inventory/tools.ndjson",
      "field": "config.api_key",
      "category": "api_key",
      "replacement": "[REDACTED:api_key:sha256=abcdef123456]"
    }
  ]
}
```
