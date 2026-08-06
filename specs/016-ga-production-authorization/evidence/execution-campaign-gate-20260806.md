# DuckDock 2.0 GA external-execution campaign gate — 2026-08-06

## Scope and truth boundary

This record covers repository automation that prepares a release-authority
execution plan for the real GA collectors. It does not assert that the release
authority reviewed or approved the plan, that a target was mutated, that any
external evidence exists, or that DuckDock 2.0 is production GA.

## Closed operational gap

The individual collectors already fail closed on their own target, release,
policy, signer, safety acknowledgement and evidence schema. Operators still had
to copy the same release/target identity across many commands, invent dozens of
raw/signature/wrapper paths, remember the safe ordering for mutations and then
copy the nine final paths into a separate preapproval request. A typo or stale
path could consume a short evidence-freshness window; running capacity before
network/secrets stability, HA before state-service evidence, or destructive
restore against the production target was an avoidable operational risk.

`prepare_ga_execution_campaign.py` now consumes
`duckdock-ga-execution-campaign-request-v2` plus an independently supplied
topology PASS receipt. It reopens and revalidates the receipt, manifest, nine
policies and trust stores, then content-addresses:

- the exact `2.0.0` commit, contract and immutable backend/frontend images;
- the named HTTPS production Kubernetes HA target and fault domains;
- exact Kubernetes context/Namespace and a distinct recovery/staging target;
- a content-addressed backup-manifest allowed-signers file;
- a fresh, previously nonexistent evidence root;
- a timezone-aware execution window of no more than 14 days;
- the generated preapproval request digest.

The emitted `duckdock-ga-execution-campaign-v2` validates a 12-phase dependency
graph and assigns all 67 planned artifacts exactly once, including signed tag,
source archive, SLSA/SPDX/registry predicates, raw SARIF and scans. Capacity depends on
readiness, network and secrets; HA depends on readiness, network and signed
state-service evidence; the final assembly depends on release provenance and
all nine target wrappers plus the later execution-closure receipt. External-vantage, mutating, destructive and
disruptive phases carry the exact target acknowledgement they require. Phase
roles must exist in the verified trust topology.

Every phase is initially `PENDING_EXTERNAL_EVIDENCE`, the campaign status is
only `PLANNED_EXTERNAL_EXECUTION`, and the plan states
`does_not_authorize_GA_or_target_mutation`. It therefore coordinates external
work without manufacturing a PASS or bypassing the release authority. The tool
also emits the exact `duckdock-ga-preapproval-assembly-request-v1` consumed by
the formal campaign-closure gate after all expected files exist, eliminating a
second manual projection.
Both outputs are atomic, non-overwriting, outside the fresh evidence root and
are reopened with all inputs independently reverified before success.

## Negative coverage

Automated tests prove that:

- a valid topology and release/target request produce one immutable pending
  plan and a digest-matching preapproval request;
- the dependency graph contains the required capacity, HA and final-assembly
  ordering, and all artifact paths remain inside the uncreated evidence root;
- a forged topology receipt is rejected even when its JSON still says PASS;
- a destructive recovery target equal to production is rejected;
- an already-existing evidence root is rejected;
- rerunning against either existing output cannot overwrite campaign metadata.

## Verification performed

```text
backend pytest: 1215 passed, 19 skipped, 3 warnings
production authorization suite: 114 passed
production operations config suite: 40 passed, 2 warnings
frontend Vitest: 56 passed
frontend lint/build: PASS
production baseline: 27 PASS / 0 BLOCK
mypy: 81 errors <= baseline ceiling 82
Ruff: PASS
Python compile: PASS
GA CLI import/help: PASS
JSON/YAML parse: PASS
Compose config: PASS
git diff check: PASS
```

## Remaining external execution

The release authority must replace every example value, provision the real
topology, review the generated plan and explicitly authorize each target-facing
phase. Independent operators, providers, on-call staff and the assessor must
then produce and sign the listed artifacts. Only the later preapproval gate,
four-person approval campaign, final `GA_AUTHORIZED` evaluation and portable
archive verification can authorize DuckDock 2.0 GA.
