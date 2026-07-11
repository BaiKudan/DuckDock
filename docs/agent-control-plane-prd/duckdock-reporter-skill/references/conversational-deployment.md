# Conversational Deployment Guide

Use this reference when a WorkBuddy/OpenClaw user wants to deploy DuckDock reporting through chat instead of manual setup.

## Goal

The user should be able to say:

```text
帮我把这个 WorkBuddy 接入 DuckDock，每周五下午四点自动上报
```

The runtime assistant should complete:

1. explain scope;
2. collect required DuckDock settings;
3. store settings securely;
4. install or enable `duckdock_reporter`;
5. run dry-run;
6. create scheduled task;
7. run first upload if approved.

## Conversation Script

### Step 1: Intent

If the user asks to connect to DuckDock, reply:

```text
我会为当前 AI 工作台部署 DuckDock Reporter。默认只上报企业 AI 资产索引、会话摘要、产物 hash、路径和交接风险，不上传原始私聊全文、密钥、cookie 或个人系统目录。是否继续？
```

Continue only after confirmation.

### Step 2: Required Settings

Ask for missing values only:

```text
请提供 DuckDock API 地址、Runtime ID 和 Reporter Token。Token 只会写入本机安全配置，之后不会在对话里回显。
```

Required fields:

```text
DUCKDOCK_API_BASE
DUCKDOCK_RUNTIME_ID
DUCKDOCK_REPORT_TOKEN
```

Optional fields:

```text
DUCKDOCK_WORKSPACE_ROOT
DUCKDOCK_REPORT_TYPE
DUCKDOCK_INCLUDE_RAW
```

Default:

```text
DUCKDOCK_REPORT_TYPE=weekly
DUCKDOCK_INCLUDE_RAW=false
Schedule=Friday 16:00 local time
```

### Step 3: Secure Storage

Prefer in order:

1. runtime native secret/config store;
2. OS user secret store;
3. restricted config file readable only by the current user;
4. environment variables in the scheduled task.

Do not write the token to plain logs. Do not show it again after storing.

Suggested config shape:

```json
{
  "api_base": "https://duckdock.example.com/api/v1",
  "runtime_id": 12,
  "report_type": "weekly",
  "schedule": "FRI 16:00",
  "workspace_root": null,
  "include_raw": false,
  "token_ref": "runtime-secret://duckdock_report_token"
}
```

### Step 4: Install Or Enable Skill

If the runtime has a skill registry:

```text
Install duckdock_reporter from DuckDock or local package.
Enable it for the current runtime/workspace.
Pin version 0.2.0 or newer.
```

If the runtime only supports local skills:

```text
Copy the duckdock_reporter skill folder into the runtime's skill directory.
Refresh/reload skills.
Verify that duckdock_reporter is discoverable.
```

If the runtime cannot install skills automatically, provide a short manual checklist and stop before scheduling.

### Step 5: Dry Run

Run:

```text
duckdock_reporter deploy --dry-run
```

or ask the runtime to execute the equivalent built-in action:

```text
生成 DuckDock Pack 试运行，不上传。
```

Report only counts:

```text
试运行完成：
- assets: 18
- sessions: 42
- memories: 6
- artifacts: 27
- redactions: 5
- output: duckdock-pack-v1.zip
未上传。是否创建每周五 16:00 的定时上报任务？
```

Never show raw conversation text or secrets in the dry-run summary.

### Step 6: Schedule

Prefer native runtime scheduled tasks:

```text
Task name: DuckDock Weekly Reporter
Skill/action: duckdock_reporter run
Schedule: Friday 16:00 local time
Retry: 3 attempts, 10 minutes apart
Timeout: 30 minutes
Mode: summary_index
```

Fallback to host scheduler only when the runtime has no native scheduler.

### Step 7: First Upload

Ask:

```text
定时任务已创建。是否现在执行一次测试上传？
```

If approved:

1. generate pack;
2. create DuckDock upload session;
3. PUT to presigned URL;
4. finalize;
5. show report id and status.

Example response:

```text
测试上传完成：
- report_id: rpt_20260522160000_xxxx
- status: uploaded
- collection_job_id: 123
DuckDock 会在后台解析并入库。
```

## OpenClaw Deployment Pattern

Use OpenClaw-native capabilities when possible:

```text
openclaw skill install duckdock_reporter
openclaw config secret set DUCKDOCK_REPORT_TOKEN
openclaw task create "DuckDock Weekly Reporter" --skill duckdock_reporter --action run --cron "0 16 * * 5"
openclaw task run "DuckDock Weekly Reporter" --dry-run
```

If exact commands differ, adapt to the runtime's real command/API names. Preserve the same logical steps.

## WorkBuddy Deployment Pattern

Use WorkBuddy-native automation when available:

```text
Install or enable duckdock_reporter.
Store DuckDock config in WorkBuddy secure settings.
Create a WorkBuddy automation:
  trigger: weekly Friday 16:00
  action: run duckdock_reporter in run mode
  retry: 3
  timeout: 30 minutes
```

If WorkBuddy lacks a visible scheduler, create a Windows Task Scheduler fallback only after the user confirms.

Fallback task properties:

```text
Name: DuckDock Weekly Reporter
Trigger: Weekly, Friday, 16:00
Action: launch WorkBuddy/OpenClaw runner with duckdock_reporter run
Run as: current user or approved service account
Network: required
Retry: 3 times
```

## User-Facing Commands

The user should not need to know internal commands. Support these natural phrases:

```text
帮我接入 DuckDock
安装 DuckDock Reporter
先试运行一下，不上传
每周五下午四点自动上报
改成每天晚上八点上报
暂停 DuckDock 上报
恢复 DuckDock 上报
立刻上传一次周报
把这台 WorkBuddy 从 DuckDock 解绑
```

## Deactivation

When the user asks to pause or uninstall:

1. disable the scheduled task;
2. keep local config unless the user asks to delete it;
3. revoke or rotate the DuckDock token if requested;
4. do not delete already-uploaded DuckDock records.

When the user asks to remove completely:

1. disable and delete the scheduled task;
2. delete local DuckDock reporter config;
3. remove the local skill if supported;
4. advise the DuckDock admin to revoke the Runtime Report Token.

## Safety Constraints

- Do not deploy silently.
- Do not enable raw upload by default.
- Do not scan outside runtime/workspace roots.
- Do not modify existing user automations unless they are clearly DuckDock reporter tasks.
- Do not claim upload success until finalize returns successfully.
- Do not treat inability to find WorkBuddy private paths as failure; ask the user to select workspace root.
