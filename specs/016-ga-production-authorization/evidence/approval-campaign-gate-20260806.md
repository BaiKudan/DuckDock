# DuckDock 2.0 approval campaign gate — 2026-08-06

## Scope and boundary

This record covers repository automation for the Product, Architecture,
Security and Operations approval campaign. It does not assert that any real
person reviewed or approved DuckDock 2.0, and it is not a production
authorization. Real approvals remain an external organizational act.

## Closed operational gaps

- The approval signer no longer accepts a caller-provided release digest. It
  requires the approval-empty authorization bundle and the out-of-band
  release-authority policy, reruns the authoritative evaluator, and proceeds
  only at `APPROVAL_COLLECTION` with every foundation/evidence check passing.
- Every signer must use the same approval-empty campaign base. The tool checks
  that the identity is authorized for exactly the requested role, derives the
  current release digest, signs role/identity/decision/time in namespace
  `duckdock-ga`, and immediately verifies that new signature through the same
  evaluator and policy trust store.
- Each signer emits three immutable files: the OpenSSH signature, the exact
  approval entry, and `duckdock-ga-approval-preflight-v1`, which records the
  authorization/policy digests and full local gate result. Existing output
  paths are never overwritten.
- `finalize_ga_authorization.py` accepts exactly four distinct role/identity
  entries and an approval-empty base. It refuses missing signatures, duplicate
  roles, a cross-release digest, stale approval, wrong key, changed evidence,
  output-path rebasing and any non-authorized result.
- The final authorization is written only after an in-memory `GA_AUTHORIZED`
  result and is then reopened and evaluated again. A
  `duckdock-ga-authorization-finalization-v1` receipt is created only when the
  persisted file remains `GA_AUTHORIZED` with zero blocks.

## Negative coverage

Automated tests prove that signing/finalization rejects:

- evidence that is missing before an approver signs;
- a Product identity signing with the Architecture private key;
- a copied or modified release digest in one of four entries;
- incomplete/duplicate approval sets and any final result below
  `GA_AUTHORIZED` through the existing evaluator tests;
- final output that cannot be re-opened and reverified.

## Verification performed

```text
backend pytest: 1188 passed, 19 skipped, 3 warnings
production authorization suite: 87 passed
production operations config suite: 40 passed
production baseline: 22 PASS / 0 BLOCK
Ruff: PASS
Python compile: PASS
approval CLI import/help: PASS
Bash syntax: PASS
git diff check: PASS
```

## Remaining external execution

The release authority must still provision the real policy/trust store and
read-only evidence bundle. Four distinct accountable people must independently
invoke the signer after reviewing the exact target evidence, and the resulting
bundle must be finalized and archived. No repository test or project agent can
substitute for those decisions.
