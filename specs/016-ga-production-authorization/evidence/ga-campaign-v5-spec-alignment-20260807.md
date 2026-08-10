# GA campaign v5 specification alignment — 2026-08-07

## Scope

This evidence records the correction of a release-contract drift discovered
after the campaign-bound Kubernetes target deployment gate was implemented.
The executable campaign, request template, closure, progress inspector,
runbook and tests already used campaign v5, but the authoritative acceptance
criteria still described parts of campaign v4.

This is a repository contract correction. It does not assert that any external
production evidence, third-party assessment or organizational approval exists.

## Drift found

The stale acceptance criteria still named or counted:

- campaign request/plan/closure v4;
- 91 external artifacts instead of 102;
- 14 external phases instead of 15;
- eight phase-start pairs and guarded production CLIs instead of nine;
- three Kubernetes live identity/access guards instead of four;
- no formal PGA-20 local-preflight criterion;
- no formal PGA-21 campaign-bound target-deployment criterion.

An external assessor or release authority following that specification could
therefore have reviewed an obsolete artifact graph even though the executable
tools correctly failed closed on campaign v5.

## Correction

The authoritative specification now matches the implemented contract:

- `duckdock-ga-execution-campaign-request-v5`;
- `duckdock-ga-execution-campaign-v5`;
- `duckdock-ga-execution-campaign-closure-v5`;
- 105 planned artifacts, of which 102 are externally produced and three are
  closure outputs;
- 16 total phases, of which 15 precede the internal preapproval assembly;
- nine acknowledged risk phases and nine signed phase-start pairs;
- nine guarded target-production entrypoints;
- four Kubernetes-facing entrypoints with live identity/access rechecks;
- PGA-20 for the non-authorizing final-tag local preflight;
- PGA-21 for the campaign-bound three-stage Kubernetes HA deployment.

PGA-21 explicitly binds the target bundle, fresh preflight and completed or
incomplete deployment receipt to the same signed campaign, release provenance,
cluster identity/access, fault domains, Secret/TLS/RWX/egress scope, phase
permit and external change request. It preserves the existing rule that an
incomplete migration or rollout receipt is immutable and the database is not
automatically rolled back.

## Anti-regression gate

`scripts/verify-production-baseline.py` now includes
`ga_campaign_spec_alignment`. The check requires the v5 schemas, current
artifact/phase/guard counts and PGA-20/PGA-21 markers, and rejects the known v4
schema/count wording. `backend/tests/test_prod_ops_config.py` independently
asserts the same current/stale marker sets.

Future campaign schema or graph changes must therefore update the executable
contract and authoritative acceptance specification together before the
production baseline can pass.

## Verification

- GA production authorization suite: `136 passed`.
- `backend/tests/test_prod_ops_config.py`: `43 passed`.
- Production repository baseline: `40/40 PASS`, including
  `ga_campaign_spec_alignment`.
- Ruff: PASS.
- Python compileall: PASS.
- `git diff --check`: PASS.
- Search for the known stale v4/count/phase wording in the authoritative spec:
  zero matches.

## Truthful remaining boundary

This correction makes the external execution contract unambiguous, but all
real GA inputs remain external: final tag and registry evidence, production
deployment/TLS/Secrets/network/backup/on-call receipts, target capacity and
growth, fault-domain and managed-state failover, independent security testing,
and four-role approval. No local specification or test result substitutes for
those artifacts.
