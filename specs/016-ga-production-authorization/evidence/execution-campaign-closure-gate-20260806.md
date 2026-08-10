# DuckDock 2.0 GA execution-campaign closure gate — 2026-08-06

## Scope and truth boundary

This record covers repository automation that closes the planned external
execution campaign and creates an approval-empty authorization. It does not
assert that a real release authority approved a campaign, that a production
target was exercised, that any real organizational signature exists, or that
DuckDock 2.0 is GA.

## Closed operational gap

The immutable campaign previously allocated every output path but did not prove
that the files later consumed by the preapproval assembler were the files
allocated by that campaign. An operator could accidentally substitute a stale
wrapper or a raw report outside the evidence root. Recovery also depended on a
backup-manifest allowed-signers file that was not bound by the plan.

`close_ga_execution_campaign.py` now:

- independently regenerates and byte-compares the persisted campaign and
  generated preapproval request;
- reopens the topology receipt, manifest, nine policies and their trust stores;
- content-addresses the separate backup-manifest allowed-signers input in the
  campaign request;
- permits closure only inside the campaign execution window and requires all
  ten final evidence observations to fall inside that window;
- requires all 64 externally produced files at the 64 exact planned paths;
- recursively follows only explicit `path+sha256`,
  `path+manifest_sha256`, `allowed_signers_path+sha256` and `signature_path`
  references, rejecting missing, symlinked, digest-conflicting or unplanned
  targets without scanning a directory;
- invokes the existing fail-closed assembler only after the exact closure is
  complete and requires the authoritative evaluator to reach
  `APPROVAL_COLLECTION` with all foundation/evidence checks clean;
- atomically persists the approval-empty authorization, assembly receipt and
  `duckdock-ga-execution-campaign-closure-v1`; partial outputs are deleted on
  failure and immutable outputs cannot be overwritten.

The closure status is only `PREAPPROVAL_ASSEMBLED`, and its boundary remains
`does_not_authorize_GA_or_target_mutation`. Freeze, four independent approvals,
finalization, `GA_AUTHORIZED`, archive creation and independent portable archive
verification remain mandatory.

## End-to-end verification

The signed production-authorization fixture was materialized at all 64 planned
campaign paths. The test relocated and re-signed the path-bearing release,
state-service and independent-assessment source documents, then executed the
same closure and assembler used by the CLI. It captured:

```text
external planned artifacts: 64
all captured campaign/trust/evidence inputs: 88
explicit reference records: 133
resulting campaign stage: APPROVAL_COLLECTION
```

The three persisted outputs were content-addressed by the closure receipt. A
second run returned failure without changing any output. Separate negative
tests reject a missing planned file, same-directory unplanned substitution,
declared digest mismatch and final evidence observed outside the campaign
window.

## Verification performed

```text
backend pytest: 1221 passed, 19 skipped, 3 warnings
production authorization suite: 120 passed
production operations config suite: 40 passed, 2 warnings
frontend Vitest: 56 passed
frontend lint/build: PASS
production baseline: 28 PASS / 0 BLOCK
mypy: 81 errors <= baseline ceiling 82
Ruff: PASS
Python compile: PASS
GA CLI import/help: PASS
JSON/YAML parse: PASS
Compose config: PASS (through production baseline)
git diff check: PASS
```

## Remaining external execution

The release authority must still create the real `v2.0.0` tag and immutable
images, provision the real trust topology, review the generated campaign and
authorize each target-facing phase. Independent operators, providers, on-call
staff and the assessor must produce the real 64 external artifacts. Only then
may the closure tool create the preapproval base for the real four-person
approval campaign.
