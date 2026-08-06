# DuckDock 2.0 approval campaign freeze — 2026-08-06

## Scope and truth boundary

This record covers repository automation that freezes and binds one future
Product, Architecture, Security and Operations approval collection window. It
does not claim that a real target was assessed, that any accountable person
approved DuckDock 2.0, or that a production GA authorization exists.

## Closed gap

The earlier signer required the same approval-empty authorization and policy,
but a release operator still had to tell four signers which logical collection
round they belonged to. Two rounds concerning the same release digest could
therefore produce individually valid entries without a signed common campaign
identifier.

`freeze_ga_approval_campaign.py` now reruns the authoritative gate and emits an
immutable `duckdock-ga-approval-campaign-freeze-v1` receipt only when the base
has no approvals and is exactly at `APPROVAL_COLLECTION`. The receipt binds:

- the exact authorization path and SHA-256;
- the exact release-authority policy path and SHA-256;
- the evaluator-derived release digest;
- a canonical campaign ID;
- freeze and expiry times (24 hours by default, at most 72 hours);
- the complete evidence-ready evaluation.

The signer requires that receipt, revalidates every binding and current
evidence, and signs the campaign ID and freeze receipt digest in
`duckdock-ga-approval-statement-v2`. It refuses signatures before the freeze or
after expiry. Per-signer receipts are now
`duckdock-ga-approval-preflight-v2`.

The finalizer independently requires the same receipt, rejects expired
campaigns, out-of-window timestamps and entries from another campaign, then
retains the binding in `duckdock-ga-authorization-finalization-v2`. The core
authorization evaluator requires all signed entries to carry one common
well-formed campaign ID and freeze digest. The final authorization itself
content-addresses the freeze receipt; every evaluation reopens that receipt and
its original approval-empty base, validates the policy and digests, and compares
all non-approval fields. Direct JSON assembly therefore cannot invent a freeze,
hide a mixed campaign or bypass the finalizer.

## Negative coverage

Automated tests prove that:

- a freeze receipt cannot overwrite an earlier receipt;
- a signer cannot use an expired, mutated or input-mismatched freeze;
- evidence removed after freeze is caught by the signer's fresh evaluation;
- a validly re-signed approval from another campaign leaves the authorization
  at `APPROVAL_COLLECTION` with `approval_campaign` blocked;
- deleting the freeze or changing its bound approval-empty base blocks only the
  campaign check even while all four OpenSSH role signatures remain valid;
- the finalizer rejects campaign mismatches before emitting either output;
- one cross-release digest still prevents final authorization after all
  campaign bindings are otherwise valid.

## Verification performed

```text
backend pytest: 1205 passed, 19 skipped, 3 warnings
production authorization suite: 104 passed
production operations config suite: 40 passed
production baseline: 24 PASS / 0 BLOCK
mypy: 81 errors <= baseline ceiling 82
Ruff: PASS
Python compile: PASS
approval CLI import/help: PASS
JSON/YAML parse: PASS
Bash syntax: PASS
git diff check: PASS
```

## Remaining external execution

The release authority must still create the real signed tag/images and target
evidence, freeze the final approval-empty bundle, distribute the same receipt,
and obtain four independent human decisions before the expiry. The final
authorization, portable archive and out-of-band archive digest still have to be
produced and published by that real release activity.
