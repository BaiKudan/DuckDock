# WorkBuddy Windows Collection Guide

Use this reference only when the runtime provider is `workbuddy` and the host OS is Windows.

## Discovery Order

1. Read explicit configuration:

```text
DUCKDOCK_WORKSPACE_ROOT
WORKBUDDY_HOME
WORKBUDDY_WORKSPACE
```

2. Ask WorkBuddy runtime APIs or export commands if available.

3. Locate installation metadata without reading user data:

```powershell
Get-Command workbuddy -ErrorAction SilentlyContinue
Get-ChildItem "$env:LOCALAPPDATA\Programs" -Directory -ErrorAction SilentlyContinue | Where-Object Name -Match "WorkBuddy|Buddy"
Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs" -Recurse -Filter "*WorkBuddy*.lnk" -ErrorAction SilentlyContinue
Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object DisplayName -Match "WorkBuddy|CodeBuddy|Tencent"
```

4. Verify candidate workspace roots:

```text
%LOCALAPPDATA%\Tencent\WorkBuddy
%APPDATA%\Tencent\WorkBuddy
%USERPROFILE%\.workbuddy
%USERPROFILE%\Documents\WorkBuddy
```

Only use a candidate if it contains WorkBuddy-specific metadata or a user-selected workspace marker.

## Safe Collection Boundary

Do not recursively scan `%USERPROFILE%` or drive roots. Only scan:

- The app workspace root.
- Explicit project roots selected by WorkBuddy.
- Export directories created by WorkBuddy.
- Directories allowed by `DUCKDOCK_WORKSPACE_ROOT`.

## Suggested Mapping

| WorkBuddy item | DuckDock item |
|---|---|
| reusable prompt | `prompt` asset |
| reusable automation | `workflow` asset |
| local tool connector | `tool` asset |
| project memory/context | `knowledge_base` asset |
| conversation/task | `session` work trace |
| generated file | `artifact` |
| export summary/audit record | `evidence` |

## Default Privacy Mode

Use `summary_index` mode by default:

- include title, time, owner, project, summary, hashes, and artifact references;
- exclude raw transcript body;
- exclude raw memory body;
- exclude connector secrets;
- exclude screenshots unless explicitly exported by WorkBuddy for business audit.

Use `raw_allowed` mode only when all are true:

1. `DUCKDOCK_INCLUDE_RAW=true`;
2. enterprise policy permits raw evidence;
3. the specific record is business/workspace scoped;
4. redaction has been applied.

## Recommended Schedule

Use WorkBuddy's own scheduled task capability if available. Otherwise use Windows Task Scheduler:

```text
Trigger: weekly Friday 16:00
Action: run WorkBuddy/OpenClaw task that invokes duckdock_reporter
Retry: 3 times, 10 minutes apart
Run condition: user logged in or service account available
```

The scheduled task should inject DuckDock configuration as environment variables or use WorkBuddy's secure runtime config store.
