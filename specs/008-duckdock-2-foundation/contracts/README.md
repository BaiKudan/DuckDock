# DuckDock 2.0 Foundation Contracts

Status: G0 technical PASS; Product/Architecture Owner approval pending

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

## Validation

Run the focused contract suite without loading the backend application:

~~~powershell
conda run -n base python -m pytest backend\tests\test_duckdock_v2_contracts.py -q --noconftest
~~~

The repository test uses only the Python standard library so contract validation does not add a runtime dependency. CI may additionally validate the schemas with a Draft 2020-12 implementation.
