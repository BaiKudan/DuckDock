# DuckDock 2.0 GA approval-campaign closure binding — 2026-08-06

## Scope and truth boundary

This record covers the repository protocol that binds a completed external
execution campaign to every later approval and archive artifact. It does not
claim that a real release authority executed the campaign, that a customer
production target passed, that independent assessors or operators produced real
evidence, or that the four accountable humans approved DuckDock 2.0.

## Closed release gap

The v1 approval freeze content-addressed the approval-empty authorization,
approval policy, release digest and approval window, but it did not bind the
execution-campaign closure that proved where those inputs came from. A directly
assembled base could therefore enter the historical approval protocol without
proving the planned 64-artifact execution graph.

The formal protocol now uses
`duckdock-ga-approval-campaign-freeze-v2`. Before emitting an immutable freeze,
the freezer independently reopens and verifies the persisted closure against
the exact authorization and out-of-band policy. The freeze and derived campaign
ID include the closure SHA-256. Every approval statement already signs the
campaign ID and freeze SHA-256, so all four signatures now transitively bind:

```text
execution request/topology/window
  -> 64 external artifacts / 88 captured inputs / 133 explicit references
  -> preapproval authorization + assembly receipt + execution closure
  -> freeze v2 (closure + empty base + policy + release digest + window)
  -> four role-separated approval statements
  -> final GA authorization
  -> deterministic portable archive + independent digest
```

The signer and finalizer require a closure-bound v2 freeze by default. The final
authorization CLI, authorized-bundle archiver and independent archive verifier
also reject a legacy unbound v1 freeze by default. An explicit
`--allow-legacy-unbound` switch remains only so historical fixtures and old
records can still be audited; it is documented as forbidden for formal GA.

## Independent and portable verification

`verify_persisted_closure` does not trust the closure summary. On the generating
host it regenerates the campaign/request, reopens topology, policies, trust
stores and backup allowed-signers, recomputes the exact artifact and reference
closure, verifies output bindings, and reruns the authoritative evaluator at
the recorded closure time.

The v2 freeze makes the execution closure an ordinary content-addressed
reference. The archive collector therefore includes the closure, campaign and
all planned inputs automatically; it no longer depends on an operator adding
the closure as a supplemental file. During receiver-side verification, every
original path is resolved only through the already validated archive manifest.
The closure's 133-record ledger and approval-empty evaluation are recomputed
from temporary content-addressed members without host-path fallback. Path text
in diagnostics is not treated as a security verdict; all gate keys and
PASS/BLOCK outcomes remain exact.

## End-to-end verification

The signed production fixture performs the complete formal path in one test:

1. materialize and close all 64 planned external artifacts;
2. independently verify the persisted closure;
3. freeze the approval-empty base with v2 and the exact closure digest;
4. create Product, Architecture, Security and Operations signatures without a
   legacy compatibility flag;
5. finalize and persist a `GA_AUTHORIZED` authorization;
6. create an archive without naming the closure as a supplemental file;
7. delete host-path assumptions through isolated extraction and independently
   verify the archive as `GA_AUTHORIZED_ARCHIVE_VERIFIED`.

Historical v1 fixture tests use the compatibility flag explicitly. The same
validator rejects v1 whenever `require_execution_closure=true`.

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

The release authority must still generate the real signed `v2.0.0` release and
immutable images, provision separated real identities and trust stores, approve
and execute every target-facing campaign phase, obtain independent assessment
and provider/operator evidence, and have four accountable people review and
sign within the v2 freeze window. The resulting archive digest must be
published through an independent immutable channel before DuckDock 2.0 can be
called GA.
