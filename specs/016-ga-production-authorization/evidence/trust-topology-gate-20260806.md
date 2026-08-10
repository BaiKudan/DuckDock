# DuckDock 2.0 global GA trust topology gate — 2026-08-06

## Scope and truth boundary

This record covers repository automation for organizational identity and
OpenSSH public-key separation across all nine GA trust policies. It does not
assert that a release authority has provisioned real people, service identities
or keys, that target exercises were executed, or that DuckDock 2.0 is
production GA.

## Closed release gap

Each policy already enforced exact principals, unique keys and role separation
inside its own trust store. The final evaluator also separated release builders
and state-service operators from approvers. It did not prove that TLS, network,
secrets, alerting, recovery or capacity identities and public keys were unique
across the other policy files. A locally valid policy set could therefore reach
the end of expensive target exercises before revealing that one person,
service identity or key was trusted for multiple organizational duties.

`verify_ga_trust_topology.py` now accepts a content-addressed
`duckdock-ga-trust-topology-manifest-v1` containing exactly:

- approval and independent security assessment;
- release provenance;
- TLS and network probes;
- secrets provider and independent verifier;
- alert delivery and human on-call;
- recovery storage, restore executor and verifier;
- capacity load, storage observation and cleanup verification;
- state-service provider and independent verifier.

The preflight reopens each policy and its digest-bound allowed-signers file,
requires exact principal projection, unique policy IDs and schema versions,
then rejects any identity assigned to more than one duty or any public key
accepted for more than one duty. It checks all inputs again around atomic,
non-overwriting receipt persistence and emits
`duckdock-ga-trust-topology-verification-v1` only on PASS.

The receipt is an early release-authority diagnostic, not a new trust root. The
production authorization evaluator independently rebuilds the same nine-policy
topology from retained evidence and adds
`organizational_trust_separation` to the `FOUNDATION` checks. When a target
evidence file is not yet available, the topology check is explicitly deferred
so the existing `EVIDENCE_COLLECTION` stage remains truthful; once all policy
inputs are present, identity or key reuse blocks the release at `FOUNDATION`.

## Negative coverage

Automated tests prove that:

- nine internally and globally separated policy/trust-store pairs pass;
- reusing an approval public key for a different TLS identity is rejected by
  the early preflight;
- adding the Product approval identity and key to an otherwise valid TLS policy
  leaves the TLS signature check valid but makes the authoritative evaluator
  fail only `organizational_trust_separation` at `FOUNDATION`;
- missing target evidence still reports `EVIDENCE_COLLECTION`, rather than
  being incorrectly reclassified as a trust-foundation failure;
- the CLI persists and reopens an immutable PASS receipt and CI verifies the
  new operational entry point.

## Verification performed

```text
backend pytest: 1211 passed, 19 skipped, 3 warnings
production authorization suite: 110 passed
production operations config suite: 40 passed, 2 warnings
frontend Vitest: 56 passed
frontend lint/build: PASS
production baseline: 26 PASS / 0 BLOCK
mypy: 81 errors <= baseline ceiling 82
Ruff: PASS
Python compile: PASS
GA CLI import/help: PASS
JSON/YAML parse: PASS
git diff check: PASS
```

## Release assessment

The repository-side 2.0 release protocol is now strong enough to prevent an
invalid organizational trust layout from consuming the target exercise window.
This improves technical GA readiness but does not change the release decision:
the branch remains `2.0.0-rc.1`, and formal GA remains blocked until the release
authority supplies the real nine-policy topology, signed final tag/images,
real target evidence, independent assessment, four human approvals, final
`GA_AUTHORIZED` result and independently published archive digest.
