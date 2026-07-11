# DuckDock Runtime MCP Adapter

`duckdock-runtime-mcp` is a local MCP adapter for deploying DuckDock Reporter into desktop AI runtimes.

The first implementation targets Tencent WorkBuddy on Windows and macOS. It keeps DuckDock's main services decoupled from WorkBuddy internals:

- MCP is used as the standard execution surface.
- `duckdock-reporter` remains the actual periodic collection skill.
- Daily and weekly self-reports submit `duckdock-structured-report-v1` directly to DuckDock.
- Handover or audit evidence packs still use DuckDock upload sessions and presigned object-storage URLs.
- MinIO credentials are never given to WorkBuddy.

## Tools

| Tool | Purpose |
|---|---|
| `duckdock.runtime.detect` | Detect local WorkBuddy, Reporter skill, config, and WorkBuddy MCP bridge. |
| `duckdock.reporter.install` | Install or update `duckdock/duckdock-reporter` from DuckDock private registry. |
| `duckdock.reporter.configure` | Store DuckDock API base, runtime id, provider, and optional Reporter Credential locally. |
| `duckdock.reporter.run_structured` | Submit one privacy-preserving daily/weekly structured report from the local WorkBuddy runtime. |
| `duckdock.reporter.dry_run` | Create a one-time WorkBuddy automation for structured Reporter validation. |
| `duckdock.reporter.schedule` | Create a recurring WorkBuddy automation, defaulting to Friday 16:00. |
| `duckdock.reporter.status` | Return local install/config and WorkBuddy automation summaries. |
| `duckdock.reporter.pause` | Pause matching DuckDock Reporter WorkBuddy automations. |
| `duckdock.reporter.uninstall` | Remove local config, optionally remove skill files and automations. |

## WorkBuddy MCP config example

Use an absolute path in real deployments.

```json
{
  "mcpServers": {
    "duckdock-runtime": {
      "command": "python",
      "args": [
        "/absolute/path/to/DuckDock/tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py",
        "--api-base",
        "https://duckdock.example.com/api/v1"
      ]
    }
  }
}
```

After WorkBuddy loads the MCP server, a user can say:

```text
帮我把这台 WorkBuddy 接入 DuckDock。
DuckDock API: https://duckdock.example.com/api/v1
Runtime ID: <runtime_id returned by /reporters/enroll>
Reporter Credential: <dkr_report_* from /reporters/enroll>
每周五 16:00 自动上报，并现在做一次 dry-run 检查。
```

Expected MCP tool order:

```text
duckdock.runtime.detect
duckdock.reporter.install
duckdock.reporter.configure
duckdock.reporter.run_structured
duckdock.reporter.dry_run
duckdock.reporter.schedule
duckdock.reporter.status
```

## Local self-test

```powershell
python tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py --api-base https://duckdock.example.com/api/v1 --self-test
```

The self-test only detects local state. It does not upload data.

`duckdock.runtime.detect` reports two related but separate WorkBuddy facts:

- `workbuddy_mcp.callable`: DuckDock can call WorkBuddy's local connector-proxy bridge.
- `workbuddy_mcp_config.duckdock_runtime_registered`: WorkBuddy has loaded or been configured to load this DuckDock MCP server, which is required before a WorkBuddy-created automation can call `duckdock.reporter.run_structured` by itself.

Direct `duckdock.reporter.run_structured` submission and WorkBuddy automation create/delete have been validated in an isolated test environment. Full recurring autonomous execution requires registering this MCP server in WorkBuddy and reloading WorkBuddy.

## Security boundary

- The adapter does not expose MinIO credentials.
- Reporter credentials are redacted in MCP responses.
- If a credential is stored by `duckdock.reporter.configure`, it is stored in the local DuckDock MCP config file and should be runtime/user/device scoped and revocable.
- The adapter only uses WorkBuddy's high-level automation MCP bridge for scheduling. It does not rely on WorkBuddy remote-control filesystem or PTY APIs as a product contract.
- User content collection must remain inside `duckdock-reporter` privacy rules.
- Daily/weekly reports must stay summary/index oriented. Use `duckdock-pack-v1` only for explicit handover, audit, or evidence packages.
