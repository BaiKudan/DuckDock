# DuckDock Runtime MCP Adapter

`duckdock-runtime-mcp` is a local MCP adapter for deploying DuckDock Reporter
and governed execution lifecycle instrumentation into desktop AI runtimes.

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
| `duckdock.execution.session_start` / `session_complete` | Persist stable OpenClaw-like external Session correlation without transcripts. |
| `duckdock.execution.run_start` / `run_complete` | Persist external Run + OTel trace/span correlation and terminal metadata-only counters. |
| `duckdock.execution.buffer_status` | Inspect the durable ack cursor, pending age/count/bytes, retry state, pressure, and loss markers. |
| `duckdock.execution.buffer_flush` | Replay pending lifecycle envelopes in stable local sequence using the current Runtime credential. |
| `duckdock.execution.buffer_discard` | Explicitly discard an exact pending range and emit a durable `PARTIAL` / `DECLARED_LOSS` marker. |
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
duckdock.execution.session_start
duckdock.execution.run_start
duckdock.execution.run_complete
duckdock.execution.session_complete
duckdock.execution.buffer_status
duckdock.execution.buffer_flush
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

## Durable offline execution queue

Runtime MCP v0.3.0 stores every metadata-only Session/Run lifecycle envelope
in `execution-buffer.sqlite3` before its first network attempt. SQLite WAL,
`synchronous=FULL`, stable queue sequences and stable idempotency keys allow a
new MCP process to resume ordered delivery after a crash.

If a Session or Run start is offline, its result contains `queued=true` and a
`queue_sequence`. Pass that sequence as `session_queue_sequence` or
`run_queue_sequence` to dependent lifecycle calls. During replay the adapter
resolves the acknowledged server public ID before sending the dependent item.

```text
session_start -> queue_sequence 41
run_start(session_queue_sequence=41) -> queue_sequence 42
run_complete(run_queue_sequence=42) -> queue_sequence 43
buffer_flush -> ack_cursor 43
```

The default limits are 10,000 pending envelopes and 64 MiB. Override them with
`DUCKDOCK_EXECUTION_BUFFER_MAX_ITEMS` and
`DUCKDOCK_EXECUTION_BUFFER_MAX_BYTES`. The queue never evicts automatically:
hard-limit admission fails visibly. An operator discard requires an exact
range, a safe reason code and `confirm=true`; discarded payloads are blanked
and a durable range/count/reason loss marker remains.

This is at-least-once replay, not exactly-once transport. DuckDock's server-side
idempotency contract prevents duplicate domain facts. Credentials are loaded
at send time and are never stored in queue rows; a rotated credential may
replay only rows belonging to the same configured Runtime.

## Security boundary

- The adapter does not expose MinIO credentials.
- Reporter credentials are redacted in MCP responses.
- If a credential is stored by `duckdock.reporter.configure`, it is stored in the local DuckDock MCP config file and should be runtime/user/device scoped and revocable.
- The adapter only uses WorkBuddy's high-level automation MCP bridge for scheduling. It does not rely on WorkBuddy remote-control filesystem or PTY APIs as a product contract.
- User content collection must remain inside `duckdock-reporter` privacy rules.
- Daily/weekly reports must stay summary/index oriented. Use `duckdock-pack-v1` only for explicit handover, audit, or evidence packages.
- Lifecycle tools require timezone-aware event timestamps so a retry reuses the
  same envelope and idempotency key. The Runtime must inject the same
  `external_run_id`, `external_session_id`, OTel trace ID and root span ID into
  its root span; DuckDock will not guess a Run boundary from content.
- The durable buffer contains metadata identifiers and timestamps. Deploy it
  only on storage protected by the organization's endpoint/disk-encryption
  policy. `duckdock.reporter.uninstall` preserves the buffer to avoid silently
  deleting unacknowledged envelopes.
