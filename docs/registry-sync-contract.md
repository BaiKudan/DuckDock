# DuckDock Registry Sync Contract

This document defines the pull-based synchronization contract for external clients that consume DuckDock shared skills.

## Goal

Clients should be able to:

- discover currently visible shared versions
- incrementally detect changes
- remove versions that are no longer public
- recover after downtime using a cursor

The contract is intentionally split into two parts:

- `index`: current visible state
- `events`: incremental change stream

## Visibility Model

A version is publicly synchronizable only when all of the following are true:

- the publisher explicitly enabled public sharing for that version
- the version finished verification and entered `production`
- the version has not been deleted or withdrawn

States:

- `hidden`: not shared, never visible to anonymous clients
- `pending`: shared, but not yet `production`
- `live`: shared and `production`
- `tombstone`: a previously visible object should be removed by clients

## Endpoints

### 1. Anonymous current-state index

`GET /api/v1/registry/index`

Recommended client usage:

- call anonymously
- use `latest_only=false` if you need all visible versions
- use `latest_only=true` if you only want the newest visible version per skill

Anonymous behavior:

- returns only public `live` versions
- never returns `pending` or `hidden`

Useful query params:

- `namespace`
- `latest_only`
- `include_urls`
- `changed_since`

Notes:

- `changed_since` is an optimization, not a full event log
- clients should not rely on `index` alone for deletion handling

### 2. Incremental sync events

`GET /api/v1/registry/events`

Useful query params:

- `since_cursor`
- `limit`
- `namespace`
- `include_state_events`

Anonymous behavior:

- returns only public synchronization events
- by default returns only `upsert` and `tombstone`

Authenticated behavior:

- returns namespace-scoped events for the caller
- can include internal state changes such as `pending`

## Event Semantics

Each event has:

- `cursor`
- `namespace`
- `skill`
- `tag`
- `entity`
- `operation`
- `sync_state`
- `reason`
- `payload`

### Operations

- `upsert`
  - the version is currently synchronizable
  - clients should download or refresh local cache

- `state`
  - intermediate lifecycle change
  - examples: shared but still scanning
  - only useful for richer clients or dashboards

- `tombstone`
  - previously visible content is no longer valid
  - clients should remove the local visible copy and clear activation if needed

### Common event patterns

Publish shared version:

1. `state/pending`
2. `upsert/live` after verification passes

Disable public sharing:

1. `tombstone`

Delete public version:

1. `tombstone`

Delete skill with public visibility:

1. `skill`-level `tombstone`

## Recommended Client Algorithm

### Cold start

1. call `GET /registry/index`
2. materialize all returned public versions locally
3. store `next_cursor` from `GET /registry/events` or initialize cursor to `0`

### Steady-state sync

1. call `GET /registry/events?since_cursor=<cursor>`
2. process events in ascending `cursor` order
3. update local cursor only after successful processing

Processing rules:

- `upsert/live`
  - download manifest or download-link
  - validate checksum if provided
  - unpack to cache
  - optionally switch active version

- `tombstone`
  - remove local cached artifact if desired
  - if active version matches, deactivate or roll back

- `state/pending`
  - do not activate
  - optional: display as queued or staged

## Download Flow

For a specific public live version:

- `GET /api/v1/registry/namespaces/{namespace}/skills/{skill}/versions/{tag}/manifest`
- or `GET /api/v1/registry/namespaces/{namespace}/skills/{skill}/versions/{tag}/download-link`

Both endpoints are available anonymously for public live versions.

Returned URLs are short-lived signed URLs. Clients should:

- download promptly
- re-request a fresh link if the signed URL expires

## Webhook Accelerator

DuckDock emits `registry.sync.changed` to namespace webhooks.

This is not the source of truth.

Recommended usage:

- use webhook receipt only as a wake-up trigger
- immediately pull `/registry/events?since_cursor=<cursor>`

Do not treat webhook delivery itself as a durable sync channel.

## Deletion Semantics

Clients must treat tombstones as authoritative for removal.

If a version disappears from `index` but there is no tombstone yet:

- do not assume immediate deletion
- wait for the event stream or next reconciliation cycle

## Recovery Rules

If a client loses local cache or falls behind:

1. pull `events` from the last durable cursor
2. if the cursor is missing or state is inconsistent, reconcile with full `index`

Recommended fallback:

- periodic full reconciliation using `index`
- continuous incremental application using `events`

## Current Scope

Implemented now:

- public live discovery
- cursor-based incremental events
- version tombstones
- skill tombstones
- public manifest/download-link
- webhook wake-up event

Not yet formalized:

- retention-driven tombstones with policy metadata
- client-side rollback policy

Implemented after the initial version:

- namespace-aware soft delete on the management side
- skill restore / namespace restore semantics
- deleted-object management views and restore endpoints
