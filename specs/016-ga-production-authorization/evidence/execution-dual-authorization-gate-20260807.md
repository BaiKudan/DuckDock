# GA external-execution dual-authorization gate — 2026-08-07

## Outcome

DuckDock now requires campaign-bound, pre-window approval from two distinct
release-authority roles before any acknowledged external target phase can run.
The repository implements the protocol and fail-closed verification path; it
does not claim that real Security or Operations people have signed a production
campaign.

## Protocol

`prepare_ga_execution_campaign.py` plans five new immutable artifacts and a
first-class `execution_authorization` phase:

- one `duckdock-ga-execution-authorization-manifest-v1`;
- one Security and one Operations
  `duckdock-ga-execution-authorization-statement-v1`;
- one OpenSSH signature for each statement under the
  `duckdock-ga-execution-authorization` namespace.

The manifest is derived from the persisted campaign. It content-addresses the
campaign and binds its exact release, target, execution controls and the eight
acknowledged phases: TLS, network, secrets, capacity, alerting, recovery,
state-services and high availability. Each phase projection retains its risk
class, dependencies, acknowledgement, authorized policy roles, tools and
outputs. Its explicit boundary is
`authorizes_only_named_campaign_phases_not_GA_or_unlisted_mutation`.

The Security and Operations statements must both be signed after manifest
preparation and before `window_starts_at`. Signer identities are resolved from
the campaign topology's content-addressed `duckdock-ga-approval-policy-v2` and
shared allowed-signers store. The existing topology invariant ensures that
roles, identities and public keys are globally disjoint.
The production CLIs take timestamps only from the current UTC clock; they expose
no caller-controlled preparation or signing time that could backdate a late
authorization.

## Fail-closed integration

- every phase with a required acknowledgement depends on
  `execution_authorization`;
- preparation, signing and verification CLIs require the campaign-planned
  artifact paths and refuse overwrite;
- progress inspection cryptographically verifies the complete five-file set
  and marks all five invalid when either signature or statement fails;
- campaign closure re-verifies both role statements before evidence capture,
  stores a path-independent authorization verdict in the closure and rejects
  missing, late, changed, cross-campaign or wrong-role authorization;
- the authorization verifier independently regenerates the campaign/request/
  topology/assembly graph before trusting it, rejects expired campaigns and
  rechecks all inputs after signature verification to close TOCTOU changes;
- persisted closure and portable archive verification repeat the same checks
  with strict archive path overrides and no host fallback;
- the separate Security-signed third-party assessment engagement remains
  mandatory and is not replaced by this execution authorization.

## Verified test path

The signed synthetic end-to-end fixture now plans 74 artifacts: 71 external
inputs and three closure outputs. Its exact closure captures 95 inputs and 139
explicit references. The fixture reaches `APPROVAL_COLLECTION`, four-role
`GA_AUTHORIZED`, deterministic archive verification and publication
authorization. Negative coverage rejects post-signature statement changes, a
signature copied from the other role, a manually changed campaign, an expired
campaign and manifest preparation at or after the execution-window boundary.

Synthetic fixture keys and identities are test data only. Real GA still
requires the target organization's Security and Operations identities to sign
the final campaign before its window starts.

## Quality gates

- backend: 1233 passed, 19 skipped; selected core coverage 81.04% (minimum 65%);
- production-authorization suite: 131 passed;
- production-operations suite: 41 passed;
- frontend: 11 files / 56 tests, zero lint warnings, production build passed;
- dependency audit: Python and npm reported zero known vulnerabilities;
- static/contract: Ruff, compileall, OpenAPI v2 drift, 63 tracked JSON files,
  33 tracked YAML files and four Compose configurations passed;
- type ratchet: 81 mypy errors, below the ceiling of 82;
- repository production baseline: 31/31 PASS.
