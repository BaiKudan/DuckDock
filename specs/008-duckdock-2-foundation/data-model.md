# Data Model: DuckDock 2.0 Foundation Implementation Phase

**Status**: Draft

**Database boundary**: MySQL stores governance metadata only; MinIO stores trajectory/artifact bytes; external telemetry backends store raw spans.

## 1. Modeling Principles

1. Every governance record has direct tenant ownership.
2. Activated deployment snapshots and terminal Run facts are immutable.
3. External provider schemas are normalized at the boundary, not mirrored into tables.
4. Public IDs are opaque; internal IDs are implementation details, never authorization inputs.
5. Raw prompts, messages, completions, tool payloads and spans are not MySQL business data.
6. Denormalized `namespace_id` fields are intentional security/indexing boundaries and must be validated against related rows.
7. All timestamps use the repository's UTC normalization rules.

## 2. Existing Entity Changes

### RuntimeInstance

Add:

- `namespace_id` FK, indexed; nullable only during expand/backfill, non-null after contract.

Invariants:

- ReporterCredential inherits governance scope from its Runtime.
- A Runtime cannot be rebound to another Namespace after it has Deployment, Session or Run records. Migration requires an explicit audited transfer workflow outside Foundation implementation.

### AIAsset

Add:

- `namespace_id` FK, indexed; nullable only during migration.

Invariants:

- Asset ownership and source/version references must resolve to the same Namespace.
- Existing asset type/source semantics remain unchanged.

### RuntimeBinding

Add:

- `namespace_id` FK, indexed; nullable only during migration.
- Tenant-scoped unique constraint appropriate to the current asset/runtime/environment key.

Invariant:

`binding.namespace_id == runtime.namespace_id == asset.namespace_id`.

### WorkTrace

Add:

- `namespace_id` FK, indexed; nullable only during migration.

Keep all existing summary fields and dedupe semantics. WorkTrace remains a management/work summary, not an execution span tree. AgentRun may optionally reference it.

### EvidenceItem

Add:

- `namespace_id` FK, indexed; nullable only during migration.
- `work_trace_id` FK to `WorkTrace`, nullable and indexed; `ON DELETE SET NULL`.

Invariant:

When linked to WorkTrace, `evidence.namespace_id == work_trace.namespace_id`.

The typed link is the only approved Evidence-to-WorkTrace tenant-resolution path.
CollectionJob, Membership, creator identity, object URI and free-form metadata are
not tenant evidence. Historical Evidence without a trustworthy typed link remains
unresolved until explicitly remediated. See [ADR-0211](../../docs/adr/0211-evidence-worktrace-typed-link.md).

## 3. New Entities

### 3.1 AgentDeployment

Represents one immutable deployable revision of an Agent configuration.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id` | internal PK | yes | Not exposed for authorization |
| `public_id` | opaque string/UUID | yes | Globally unique |
| `namespace_id` | FK | yes | Tenant boundary |
| `runtime_id` | FK RuntimeInstance | yes | Same Namespace |
| `agent_asset_id` | FK AIAsset | no | Same Namespace; Agent root asset if modeled |
| `external_deployment_id` | bounded string | yes | Harness/platform deployment identity |
| `environment` | bounded enum/string | yes | e.g. dev/staging/prod, normalized |
| `revision` | bounded string | yes | Immutable external or DuckDock revision |
| `configuration_digest` | SHA-256 | yes | Canonical non-secret config digest |
| `status` | enum | yes | REGISTERED/ACTIVE/RETIRED/FAILED |
| `activated_at` | UTC datetime | no | Set once |
| `retired_at` | UTC datetime | no | Set once |
| `created_by_user_id` | FK User | yes | Audit actor |
| `created_at` | UTC datetime | yes | Immutable |

Constraints/indexes:

- unique `(namespace_id, runtime_id, external_deployment_id, revision)`
- index `(namespace_id, environment, status, created_at)`
- ACTIVE/RETIRED rows cannot change identity, digest or component collection.

State transitions:

```text
REGISTERED --> ACTIVE --> RETIRED
     |
     +------> FAILED
```

### 3.2 DeploymentComponent

Pins a component used by an AgentDeployment.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id` | internal PK | yes | |
| `deployment_id` | FK AgentDeployment | yes | Parent Namespace is authoritative |
| `component_key` | bounded string | yes | Stable logical key, unique within Deployment |
| `component_role` | enum/string | yes | agent/skill/model/tool/policy/memory/other |
| `ai_asset_id` | FK AIAsset | no | Same Namespace |
| `skill_version_id` | FK SkillVersion | no | Same Namespace when applicable |
| `external_version` | bounded string | no | Provider/harness version |
| `content_digest` | SHA-256 | no | Content/config fingerprint |
| `configuration_json` | bounded JSON | no | Allowlisted non-secret summary only |
| `created_at` | UTC datetime | yes | Immutable |

Constraints:

- At least one of Asset, SkillVersion, external version or content digest must identify the component.
- A logical component key is unique within one deployment.
- Component rows become immutable when the parent Deployment leaves REGISTERED.

### 3.3 AgentSession

Groups related executions from one Runtime without storing raw conversation content.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id`, `public_id` | PK + opaque ID | yes | |
| `namespace_id` | FK | yes | Derived from Reporter credential for writes |
| `runtime_id` | FK RuntimeInstance | yes | Same Namespace |
| `deployment_id` | FK AgentDeployment | no | Same Runtime/Namespace |
| `work_trace_id` | FK WorkTrace | no | Optional management summary link |
| `external_session_id` | bounded string | yes | Reporter identity, not a secret |
| `actor_user_id` | FK User | no | Only if resolved server-side |
| `status` | enum | yes | OPEN/ENDED/ABANDONED |
| `started_at`, `ended_at` | UTC datetime | yes/no | Monotonic lifecycle |
| `sensitivity` | enum | yes | Default restricted per project policy |
| `content_capture_mode` | enum | yes | Foundation implementation: METADATA_ONLY |
| `run_count`, `error_count` | non-negative integers | yes | Server-derived/validated completion aggregates |
| `metadata_json` | bounded JSON | no | Allowlisted low-sensitivity metadata |
| `start_idempotency_key`, `start_envelope_sha256` | bounded string + SHA-256 | yes | Canonical start replay guard |
| `completion_idempotency_key`, `completion_envelope_sha256` | bounded string + SHA-256 | no | Set once when terminal |
| `created_at`, `updated_at` | UTC datetime | yes | |

Constraints/indexes:

- unique `(namespace_id, runtime_id, external_session_id)`
- index `(namespace_id, status, started_at)`

### 3.4 AgentRun

Low-volume control record for one Agent execution attempt. It is not a span store.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id`, `public_id` | PK + opaque ID | yes | |
| `namespace_id` | FK | yes | Tenant boundary |
| `session_id` | FK AgentSession | no | Same Runtime/Namespace |
| `runtime_id` | FK RuntimeInstance | yes | Credential-derived |
| `deployment_id` | FK AgentDeployment | no | Exact execution revision |
| `work_trace_id` | FK WorkTrace | no | Optional management summary link |
| `external_run_id` | bounded string | yes | Runtime-local identity |
| `otel_trace_id` | 32 lowercase hex chars | no | Correlation only |
| `root_span_id` | 16 lowercase hex chars | no | Correlation only |
| `attempt` | positive integer | yes | Default 1 |
| `status` | enum | yes | STARTED/SUCCEEDED/FAILED/CANCELLED/TIMED_OUT |
| `trust_level` | enum | yes | CHANNEL_AUTHENTICATED/PRODUCER_ATTESTED/UNVERIFIED |
| `trust_source` | enum | yes | REPORTER/COLLECTOR/IMPORT/ADMIN; orthogonal to trust strength |
| `source_schema` | bounded string | yes | e.g. duckdock-run-envelope |
| `source_schema_version` | bounded string | yes | Versioned input contract |
| `normalizer_version` | bounded string | no | Required for external imports later |
| `content_capture_mode` | enum | yes | Foundation implementation: METADATA_ONLY |
| `started_at`, `ended_at` | UTC datetime | yes/no | End not before start |
| `duration_ms` | non-negative bigint | no | Server-derived/validated |
| `step_count` | non-negative integer | no | Aggregate only |
| `model_call_count` | non-negative integer | no | Aggregate only |
| `tool_call_count` | non-negative integer | no | Aggregate only |
| `input_token_count` | non-negative bigint | no | Aggregate only |
| `output_token_count` | non-negative bigint | no | Aggregate only |
| `error_type` | bounded enum/string | no | Classified, no raw stack trace |
| `metadata_json` | bounded JSON | no | Allowlisted low-sensitivity metadata |
| `start_idempotency_key` | bounded string | yes | Scoped to Namespace/Runtime |
| `start_envelope_sha256` | SHA-256 | yes | Canonical validated payload |
| `completion_idempotency_key` | bounded string | no | Set on completion |
| `completion_envelope_sha256` | SHA-256 | no | Set on completion |
| `created_at`, `updated_at` | UTC datetime | yes | |

Constraints/indexes:

- unique `(namespace_id, runtime_id, external_run_id)`
- unique `(namespace_id, runtime_id, start_idempotency_key)`
- unique `(namespace_id, runtime_id, completion_idempotency_key)` where supported/semantically non-null
- unique `(namespace_id, otel_trace_id)` when non-null
- indexes for `(namespace_id, started_at)`, `(namespace_id, runtime_id, started_at)`, `(namespace_id, deployment_id, started_at)`, `(namespace_id, status, started_at)`

State transitions:

```text
STARTED --> SUCCEEDED
        --> FAILED
        --> CANCELLED
        --> TIMED_OUT
```

Terminal states are immutable. An identical completion replay is an idempotent read of the existing result, not a second transition.

### 3.5 AgentRunArtifact

Indexes an immutable object stored outside MySQL.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id`, `public_id` | PK + opaque ID | yes | |
| `namespace_id` | FK | yes | Must equal Run Namespace |
| `agent_run_id` | FK AgentRun | yes | |
| `kind` | enum | yes | TRAJECTORY/EVALUATION/LOG_BUNDLE/OTHER |
| `schema_name`, `schema_version` | bounded strings | yes | ATIF later, provider-neutral now |
| `object_uri` | internal object key/URI | yes | No arbitrary external/file URL |
| `sha256` | SHA-256 | yes | Integrity |
| `size_bytes` | non-negative bigint | yes | Quota/validation |
| `sensitivity` | enum | yes | Drives access/retention |
| `redaction_policy_version` | bounded string | no | Provenance |
| `completeness` | enum | yes | COMPLETE/PARTIAL/UNKNOWN |
| `created_at` | UTC datetime | yes | Immutable |

Suggested unique key: `(namespace_id, agent_run_id, kind, sha256)`.

### 3.6 TelemetrySink

Provider-neutral Namespace configuration for an external telemetry backend.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id`, `public_id` | PK + opaque ID | yes | |
| `namespace_id` | FK | yes | Tenant-scoped |
| `provider` | bounded string | yes | e.g. langfuse/otlp/custom |
| `name` | bounded string | yes | Tenant-visible label |
| `endpoint` | validated URL | yes | SSRF-safe; no embedded credentials |
| `project_ref` | bounded string | no | External project identity |
| `credential_ref` | secret-manager reference | no | Never plaintext secret |
| `status` | enum | yes | ACTIVE/DISABLED/ERROR |
| `config_json` | bounded JSON | no | Non-secret adapter config |
| `created_at`, `updated_at` | UTC datetime | yes | Changes audited |

Constraints:

- unique `(namespace_id, name)`
- no synchronous network access in model validation or Run transactions.

### 3.7 TraceBackendRef

Correlates AgentRun to a trace in a configured backend.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id` | PK | yes | |
| `namespace_id` | FK | yes | Same as Run and Sink |
| `agent_run_id` | FK AgentRun | yes | |
| `telemetry_sink_id` | FK TelemetrySink | yes | |
| `external_trace_id` | bounded string | yes | Provider identity |
| `external_session_id` | bounded string | no | Optional provider grouping |
| `trace_url` | validated URL | no | Adapter-generated, treated as untrusted display link |
| `status` | enum | yes | PENDING/CONFIRMED/ERROR/STALE |
| `last_confirmed_at` | UTC datetime | no | |
| `last_error_code` | bounded string | no | No secret/error body |
| `created_at`, `updated_at` | UTC datetime | yes | |

Constraints:

- unique `(telemetry_sink_id, external_trace_id)`
- optional unique `(agent_run_id, telemetry_sink_id)` if one trace per Sink is enforced
- all three tenant fields must match at service boundary.

### 3.8 GenericTraceProjection

Metadata-only reconciliation state for one authenticated Generic OTLP trace.
Raw spans, events and attribute documents are never stored in this table.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id`, `public_id` | PK + opaque ID | yes | |
| `namespace_id`, `runtime_id` | FK | yes | Derived from ReporterCredential |
| `telemetry_sink_id` | FK TelemetrySink | yes | Same Namespace |
| `agent_run_id` | FK AgentRun | no | Required only when mapped |
| `external_trace_id` | 32 lowercase hex | yes | Tenant/Sink-scoped trace identity |
| `root_span_id` | 16 lowercase hex | no | One accepted root only |
| `external_run_id` | bounded string | no | Validated correlation summary |
| `status` | enum | yes | MAPPED/UNMATCHED/QUARANTINED |
| `reason_code` | bounded string | no | Required unless MAPPED; no raw body |
| `source_schema`, `source_schema_version` | bounded strings | yes | OTLP projection contract |
| `normalizer_version` | bounded string | yes | Mapping policy provenance |
| `candidate_root_count`, `observed_span_count` | non-negative integers | yes | Completeness summary only |
| `first_observed_at`, `last_observed_at` | UTC datetime | no | Late-span reference updates |
| `content_capture_mode` | enum/string | yes | Foundation value is metadata_only |
| `created_at`, `updated_at` | UTC datetime | yes | |

Constraints:

- unique `(namespace_id, telemetry_sink_id, external_trace_id)`
- MAPPED requires `agent_run_id` and no reason; UNMATCHED requires no Run and
  a reason; QUARANTINED requires a reason and never moves a Run.
- duplicate and late OTLP batches may update observation summary only; they
  cannot reopen or rewrite a terminal AgentRun.

### 3.9 PackImport

Metadata-only lifecycle state for one Reporter-authenticated immutable
`duckdock-pack/1.0` upload. The archive and ATIF bytes remain in MinIO; MySQL
stores only validated identity, checksums, counts, safe status and reason codes.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id`, `public_id` | PK + `pki_` opaque ID | yes | |
| `namespace_id`, `runtime_id` | FK | yes | Derived from ReporterCredential |
| `reporter_credential_id` | FK | yes | Channel identity used at import creation |
| `agent_run_id` | FK AgentRun | no | Present only after reliable existing-Run mapping |
| `pack_id` | bounded producer ID | yes | Idempotency scope is Namespace + Runtime + Pack ID |
| `status` | enum | yes | PENDING_VALIDATION/VALIDATING/IMPORTED/IMPORTED_PARTIAL/QUARANTINED/REJECTED |
| manifest/producer fields | bounded strings + SHA-256 | yes | Validated canonical summary only; no raw manifest JSON |
| Run/Deployment/trace fields | bounded IDs | no | Correlation signals; conflicts quarantine |
| `staging_object_key` | internal object key | yes | Content-addressed immutable source Pack in MinIO |
| upload/multipart fields | enum, opaque upload ID, part size, completion time | yes/no | SINGLE_PUT compatibility or resumable MULTIPART; upload ID is never a credential |
| expected/actual Pack SHA and size | SHA-256 + positive bigint | yes/no | COMPLETE is impossible until both match |
| payload/verified counts | bounded integers | yes | Verified count cannot exceed declared count |
| `loss_reason` | bounded code | no | Required for IMPORTED_PARTIAL |
| trust/content fields | fixed strings | yes | CHANNEL_AUTHENTICATED + IMPORT + metadata_only |
| redaction provenance | bounded version/digest | no | Producer receipt is not Namespace authorization |
| `last_error_code` | bounded code | no | No raw body, archive entry or secret echo |
| `expires_at`, validation/import timestamps | UTC datetime | yes/no | Interrupted pending uploads expire by policy |

Constraints:

- unique `(namespace_id, runtime_id, pack_id)` and unique content-addressed
  upload object key;
- only IMPORTED/IMPORTED_PARTIAL may have a Run and verified payload count equal
  to the manifest payload count;
- IMPORTED_PARTIAL requires an explicit loss reason;
- checksum integrity never upgrades trust to PRODUCER_ATTESTED.

### 3.10 PackImportArtifact

Association from one verified manifest payload path to one immutable
`AgentRunArtifact`. It exists only after all Pack/payload preflight checks pass.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id` | PK | yes | |
| `pack_import_id` | FK PackImport | yes | cascade only with import metadata deletion |
| `agent_run_artifact_id` | FK AgentRunArtifact | yes | final MinIO object metadata |
| `payload_path` | safe relative POSIX path | yes | unique per import |
| `created_at` | UTC datetime | yes | |

### 3.11 AdapterHandshake

Credential-derived dynamic capability negotiation for one adapter instance.
It stores descriptor hashes and bounded operational metadata, never a token,
endpoint credential or raw Agent content.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id`, `public_id` | PK + `hs_` opaque ID | yes | Public responses use only `public_id` |
| `namespace_id`, `runtime_id`, `reporter_credential_id` | FK | yes | Server-derived identity |
| `profile`, adapter/source schema IDs and versions | bounded enum/strings | yes | Three supported profiles |
| `instance_id`, `boot_id` | bounded strings | yes | Stable install vs process boot identity |
| nonce/descriptor/config fingerprints | SHA-256 | yes/yes/no | No original nonce or config body |
| accepted/rejected capabilities | bounded JSON string arrays | yes | Dependency-derived |
| status/drift/Collector summary | enum/bounded strings | yes/no | ACTIVE/DEGRADED/EXPIRED/SUPERSEDED |
| handshake/expiry/heartbeat timestamps | UTC datetime | yes/yes/no | |

Unique `(reporter_credential_id, instance_id, client_nonce_sha256)` enforces
same-descriptor replay and changed-descriptor conflict. A newer nonce
supersedes the prior active handshake for the same credential/profile/instance.

### 3.12 AdapterHeartbeatRecord

Append-only metadata history for one handshake. Each row stores status,
config-drift classification, optional Collector status/version and observed
time. Fleet reads the latest 20 rows per handshake through a windowed query.
The table cannot store heartbeat bodies, secrets or telemetry content.

### 3.13 PackBatchStream / PackBatchReceipt

`PackBatchStream` is credential-scoped by `(reporter_credential_id,
stream_key)` and stores only the greatest contiguous `ack_cursor`.
`PackBatchReceipt` is immutable and unique by both `(stream, sequence)` and
`(stream, idempotency_key)`; it binds the canonical request SHA, durable
accepted/rejected disposition, safe reason code and optional `PackImport`.
Out-of-order receipts may exist above the cursor, but cannot advance it across
a gap. Retryable dependency failures create no receipt.

### 3.14 PackExport

Durable metadata index for a server-generated immutable ATIF Pack. It binds
Reporter credential, Namespace/Runtime/Run, idempotency key and canonical
request hash to one MinIO object key, Pack SHA-256, positive size and payload
count. Source trajectory bytes are re-read and checked against their Artifact
size/SHA before export; MySQL never stores the Pack or ATIF JSON.

### 3.15 Pack multipart extension

`PackImport.upload_mode` is `SINGLE_PUT` or `MULTIPART`. Multipart state stores
the opaque object-store upload ID, requested part size and completion time.
Resume state is read from object storage rather than trusting caller-declared
parts. Completion requires contiguous receipts, exact expected part sizes and
whole-object size/SHA verification before the existing import FSM can register
an Artifact.

### 3.16 OutboxEvent

Durable record of a committed domain event awaiting asynchronous dispatch.

| Field | Type/shape | Required | Notes |
|---|---|---:|---|
| `id` | PK | yes | |
| `event_id` | opaque UUID | yes | Globally unique consumer dedupe key |
| `namespace_id` | FK | yes | Event tenant |
| `aggregate_type` | bounded string | yes | AgentSession/AgentRun/AgentRunArtifact |
| `aggregate_public_id` | bounded string | yes | No internal authorization meaning |
| `event_type` | bounded enum/string | yes | Allowlisted |
| `schema_version` | bounded string | yes | Event contract |
| `payload_json` | bounded JSON | yes | Routing and low-sensitive summary only |
| `idempotency_key` | bounded string | yes | Unique logical event key |
| `status` | enum | yes | PENDING/LEASED/PUBLISHED/FAILED |
| `occurred_at` | UTC datetime | yes | Domain occurrence |
| `available_at` | UTC datetime | yes | Retry scheduling |
| `lease_owner`, `lease_expires_at` | bounded string/datetime | no | Worker claim |
| `published_at` | UTC datetime | no | |
| `attempt_count` | non-negative integer | yes | Default 0 |
| `last_error_code`, `last_error` | bounded strings | no | Sanitized and truncated |
| `created_at`, `updated_at` | UTC datetime | yes | |

Constraints/indexes:

- unique `event_id`
- unique `idempotency_key`
- dispatch index `(status, available_at, lease_expires_at)`
- payload size and key allowlist enforced before transaction commit.

## 4. Relationships

```text
Namespace
  |-- RuntimeInstance
  |     |-- ReporterCredential
  |     |-- AgentDeployment --< DeploymentComponent
  |     |-- AgentSession --< AgentRun
  |     +--------------------< AgentRun
  |
  |-- AIAsset --< RuntimeBinding >-- RuntimeInstance
  |       |
  |       +-- DeploymentComponent
  |
  |-- WorkTrace --< EvidenceItem
  |       ^
  |       +-- optional AgentRun link
  |
  |-- TelemetrySink --< TraceBackendRef >-- AgentRun
  |       +----------< GenericTraceProjection >-- AgentRun?
  |-- AgentRun --< AgentRunArtifact
  |       |-- PackExport
  |       +-- EvaluationResultReplayed (Outbox)
  |-- PackBatchStream --< PackBatchReceipt >-- PackImport?
  +-- OutboxEvent
```

## 5. Tenant Backfill Rules

Resolution is deterministic and repeatable. Apply only these evidence classes, in order:

1. Existing explicit, valid direct Namespace value on a rerun.
2. A verified direct FK to a Namespace-bearing Skill/SkillVersion or equivalent typed source.
3. Exactly one Namespace from explicit `AssetOwnership` records.
4. Runtime from RuntimeBindings only when every resolved bound Asset yields the same single Namespace.
5. RuntimeBinding from its already-resolved Runtime and Asset only when both agree.
6. WorkTrace from its resolved Asset; otherwise from resolved Runtime only when no conflicting relation exists.
7. EvidenceItem from its resolved WorkTrace.

Never infer from:

- a user's current or historical Namespace Membership;
- display names, email domains, provider names or URL hosts;
- free-form JSON/metadata strings;
- a global “default” Namespace;
- the first candidate returned by a query.

Results:

- `resolved`: exactly one supported Namespace, with resolution rule recorded in audit output.
- `unresolved`: no supported evidence; leave null during expand phase.
- `conflict`: two or more supported candidates; leave null and list candidates.

Contract migration requires zero unresolved/conflict rows for all five target tables.

## 6. Canonical Envelope and Idempotency

1. Parse with strict schema; reject unknown or content-bearing fields.
2. Normalize timestamps to UTC, enum casing, absent optionals and numeric bounds.
3. Build canonical JSON with sorted keys and a fixed serializer version.
4. Hash UTF-8 bytes using SHA-256.
5. Scope idempotency keys by Namespace, Runtime and operation.
6. On duplicate: equal hash returns existing resource; different hash is 409 and audit.

Store the canonicalizer/schema version so future serializer changes cannot reinterpret old hashes.

## 7. Data Retention and Sensitivity

- Deployment and release provenance: retain according to governance/audit policy; normally long-lived and immutable.
- Session/Run indexes: retain long enough for incident, evidence and release-policy windows; deletion must preserve required audit tombstones.
- Outbox published rows: retain for a bounded dedupe/forensics window, then archive/purge by policy.
- TraceBackendRef: may outlive provider retention but must show STALE when target is no longer confirmed.
- Artifact bytes: MinIO lifecycle by sensitivity and evidence hold; MySQL keeps checksum/tombstone metadata when required.
- Metadata defaults to restricted. No field is treated as safe merely because it is called `metadata`.

## 8. Post-Foundation Model Ownership

Foundation intentionally did not add evaluation or release-evidence models.
The later Evaluation Hub specification now owns the implemented
`EvaluationDataset`, `Evaluator`, `Experiment`, `Evaluation`,
`EvaluationResultManifest`, `EvaluationComparison` and
`ReleaseCandidateEvaluationBinding` records in revisions 0040–0044. See
[`specs/009-evaluation-hub/spec.md`](../009-evaluation-hub/spec.md).

Span, LLMCall, ToolCall, raw Event and Dataset-item content tables remain
deliberately absent from DuckDock MySQL. Provider-hosted item/trace content
continues to follow the retention and sensitivity boundary defined by the
Evaluation Hub.
