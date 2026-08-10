# DuckDock 2.0 Foundation Contracts

Status: G0 approved; EF-01～EF-10 Foundation implementation complete; G1
performance/reconciliation technical acceptance complete; Product/Architecture
Owner approved M1/G1 on 2026-07-30

Created: 2026-07-17

This directory contains the first provider-neutral contracts for DuckDock 2.0:

| Contract | Purpose |
|---|---|
| agent-package-v2 | Immutable Agent package identity, components, provenance and policy bindings |
| telemetry-envelope-v1 | Metadata-only trace/run reference crossing the runtime trust boundary |
| evaluation-result-v1 | Reproducible evaluation subject, dataset, metrics, decision and provenance |
| release-manifest-v1 | Candidate package, target environment, fixed quality evidence, approvals and rollback |

## Versioning Rules

- Every payload carries an explicit schema version.
- A major version may remove fields, change meaning or tighten required structure.
- A minor-compatible revision may add optional fields or enum values with documented unknown handling.
- Consumers must reject an unsupported major version.
- Unknown root fields are rejected.
- Identifiers are opaque; they never grant authorization.
- SHA-256 digests use lowercase hexadecimal.
- Date-time values are UTC-aware ISO-8601 strings.

## Security Rules

- Telemetry defaults to metadata_only.
- Hidden chain-of-thought, raw prompts, completions and tool arguments are outside this contract.
- Namespace and Runtime identity are injected or confirmed by a trusted server/Collector; client values are correlation hints only.
- Secrets are represented by credential references, never plaintext.
- A release decision must bind immutable evaluation result IDs and a policy ID.
- Future runtime evidence lookup must also bind an exact release-candidate
  reference and Deployment public ID/revision. Namespace-latest evaluation is
  not a valid candidate proof.

The implemented Foundation seam lives in
`backend/app/services/release_evidence_ports.py`. Current Release Gate behavior
does not consume that seam; enforcement remains deferred to the Release
Candidate phase.

RT-06 adds the standard OTLP/HTTP JSON projection operation to
`openapi-v2.yaml`. It is authenticated with the same `execution.write`
ReporterCredential boundary, but it accepts only Collector-sanitized OTLP and
persists metadata-only Run/reference/quarantine summaries. Raw OTLP remains in
the configured trace backend.

RT-07 adds the `createPackImport`, `getPackImport` and `finalizePackImport`
operations. They freeze `duckdock-pack/1.0`, credential-derived tenant scope,
strict archive/payload integrity and the six-state import FSM. The API never
accepts raw manifest extensions, never stores archive/ATIF JSON in MySQL and
never treats a checksum as producer attestation. ATIF v1.0–v1.7 is recognized;
unknown versions and content without Namespace authorization are not
normalized.

RT-08 adds `negotiateAdapterHandshake`, `recordAdapterHeartbeat` and
`getFleetRuntimeSummary`. All supported profiles use the same strict
`duckdock-adapter/1.0` descriptor and credential-derived Runtime mapping.
Accepted capabilities depend on current server dependencies; no connection or
storage dependency means no capability claim. Fleet responses use opaque
Runtime/handshake IDs and expose only version, capability, heartbeat,
Collector, drift and bounded workload metadata.

NEXT-003/revision 0038 adds resumable Pack multipart state/part completion,
durable per-item batch acknowledgements with a contiguous cursor, immutable
ATIF Pack export and idempotent `EvaluationResultReplayed` Outbox requests.
Evaluation replay references an already registered, size/SHA-verified
`EVALUATION` artifact; Dataset/Evaluator/Evaluation execution models remain
outside this contract until NEXT-004.

## Validation

Run the focused contract suite without loading the backend application:

~~~powershell
conda run -n base python -m pytest backend\tests\test_duckdock_v2_contracts.py -q --noconftest
~~~

The repository test uses only the Python standard library so contract validation does not add a runtime dependency. CI may additionally validate the schemas with a Draft 2020-12 implementation.
