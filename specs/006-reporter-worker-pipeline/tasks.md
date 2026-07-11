# 006 Tasks

## L0 - Contract Cleanup

- [x] Record the architecture decision: Reporter credential is long-lived,
      upload URL is short-lived, worker pipeline is the production path.
- [x] Add user self-service Reporter enrollment.
- [x] Add Reporter heartbeat endpoint.
- [x] Make upload finalize worker-first by default and remove direct-ingest
      enqueue from finalize.
- [x] Add focused backend tests.

## L1 - Dedicated Credential Model

- [x] Add `reporter_credentials` table with scopes, device id, rotation status,
      revoked reason, and last heartbeat.
- [x] Migrate existing `runtime_report_tokens` semantics or alias them cleanly.
- [x] Add token rotation endpoint that returns the new secret once and revokes
      older self-service tokens for the same device.

## L2 - Worker Schema Hardening

- [x] Version the analysis recipe/prompt/model contract in result metadata.
- [x] Validate every standard worker result file before materialization.
- [x] Expose worker result and materialization status in admin UI.
- [x] Split parsed counts, inserted counts, updated counts, and deduped counts.

## L3 - Scalable Orchestration

- [x] Keep Celery as the immediate queue implementation.
- [x] Evaluate Temporal/Prefect only when jobs need multi-step long-running
      workflow recovery beyond the existing lease protocol.
- [x] Add deployment docs for multiple analysis-worker replicas and queue
      saturation metrics.

## Open Design Questions / Current Defaults

- **Self-enrollment policy**: current default is open to every authenticated
  user/agent. Invite, approval, org-unit allowlist, or device allowlist remains
  a product policy decision.
- **Runtime cardinality**: current model creates a runtime endpoint per
  self-enrolled reporter and records `device_id` on `ReporterCredential`.
  Whether the enterprise wants one runtime per employee or one per agent/device
  remains a rollout policy decision.
- **Heartbeat retention**: current implementation keeps latest heartbeat /
  last-seen state on runtime metadata and credential rows. A historical
  heartbeat table for N-day SLA/audit trend is not implemented yet.
