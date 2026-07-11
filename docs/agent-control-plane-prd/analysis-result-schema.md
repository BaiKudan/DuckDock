# DuckDock Analysis Result Schema

## 原则

专属 OpenClaw worker 输出的是“已分析结果”，不是原始会话、原始记忆或原始文件全文。DuckDock 可以读取这些结果文件并生成控制平面索引，但 MySQL 只保存可查询、可审核、可交接的精简字段。

完整分析结果继续保存在 MinIO，通过 `analysis_result_artifacts.object_key` 回溯。

## `analysis-result.json`

总摘要文件。用于记录本次分析版本、统计和处理结论。

```json
{
  "schema_version": "duckdock-analysis-v1",
  "report_id": "rpt_xxx",
  "summary": {
    "asset_count": 12,
    "worktrace_count": 48,
    "handover_signal_count": 3,
    "risk_count": 1
  },
  "processing": {
    "redaction": "completed",
    "classification": "completed",
    "model": "openclaw:main"
  }
}
```

## `asset-cards.json`

资产卡片候选。DuckDock 会按 `runtime_id + external_id` 幂等归并到 `ai_assets`，并创建 `runtime_bindings`。

```json
[
  {
    "external_id": "skill/project-weekly-report",
    "asset_type": "skill",
    "name": "项目周报生成 Skill",
    "description": "整理项目上下文并生成周报摘要。",
    "criticality": "medium",
    "status": "active",
    "project": "project-a",
    "owner_hint": "employee",
    "confidence": 0.86
  }
]
```

支持的 `asset_type` 取值与 DuckDock 控制平面一致：`skill`、`agent`、`prompt`、`workflow`、`mcp`、`tool`、`knowledge_base`、`scheduled_task`、`credential_ref`、`workspace`、`other`。

## `worktrace-summary.md`

工作历程摘要。DuckDock 会把它作为一条工作历程摘要写入 `work_traces`，不保存原始会话正文。

```markdown
# Project A weekly analysis

- 本周主要处理客户私有化部署排查。
- 关键上下文在 project-a/runtime-openclaw。
- 未发现需要立即交接的高风险凭据。
```

## `memory-candidates.json`

长期记忆候选。DuckDock 会写入 `memory_candidates`，等待员工或管理员确认、补充或排除。

```json
[
  {
    "candidate_type": "project_context",
    "subject_type": "project",
    "subject_key": "project-a",
    "title": "Project A 私有化部署上下文",
    "summary": "该项目主要使用 OpenClaw 私有部署和 DuckDock Reporter 周报。",
    "confidence": 0.82,
    "sensitivity": "internal"
  }
]
```

## `handover-signals.json`

岗位交接和风险信号。DuckDock 会转成 `handover_signal` 或 `risk_signal` 类型的记忆候选，不直接创建复杂组织流程。

```json
[
  {
    "signal_type": "handover",
    "subject_type": "skill",
    "subject_key": "skill/project-weekly-report",
    "title": "周报 Skill 需要指定接手人",
    "summary": "该 Skill 与 Project A 周报流程相关，建议在岗位交接时确认维护人。",
    "confidence": 0.78,
    "sensitivity": "internal"
  },
  {
    "signal_type": "risk",
    "subject_type": "credential_ref",
    "subject_key": "vault://project-a/openclaw",
    "title": "存在凭据引用但未见轮换记录",
    "summary": "仅记录凭据引用和风险摘要，不保存密钥内容。",
    "confidence": 0.7,
    "sensitivity": "confidential"
  }
]
```
