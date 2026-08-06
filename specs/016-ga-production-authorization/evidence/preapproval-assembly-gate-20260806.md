# DuckDock 2.0 preapproval evidence assembly gate — 2026-08-06

## Scope and truth boundary

This record covers repository automation that turns final release provenance
and nine target evidence wrappers into an approval-empty GA authorization. It
does not assert that any real target evidence exists, that a third party
completed an assessment, that humans approved the release, or that DuckDock
2.0 is production GA.

## Closed operational gap

Previously, release operators had to copy dozens of status, timing, capacity,
recovery, HA and image fields plus ten file digests into
`production-authorization.json`. The authoritative evaluator would reject most
mistakes, but the preapproval handoff still depended on a large manual
projection and could waste a short evidence-freshness window.

`assemble_ga_preapproval_authorization.py` now consumes the versioned
`duckdock-ga-preapproval-assembly-request-v1`, which contains only:

- the exact final version, commit, immutable application images and contract;
- the target identity, URLs, fault domains and acceptance thresholds;
- paths to release provenance and exactly nine required evidence wrappers.

The release-authority approval policy cannot be selected inside that request;
it is mandatory as a separate CLI input. The assembler reopens every wrapper,
computes each SHA-256 and observed time, projects every control field, and
recomputes capacity load, database growth and cleanup values from the signed raw
receipts. It then runs the same production authorization evaluator in memory
and again after immutable output is persisted. No authorization or receipt is
left unless both evaluations are exactly at `APPROVAL_COLLECTION`, with
foundation and evidence failure lists empty. The output always has empty
`approvals` and no campaign reference, ready for the separate campaign freeze.

`duckdock-ga-preapproval-assembly-v1` records the request, out-of-band policy,
release provenance, nine evidence digests, final authorization digest and full
persisted evaluation. Inputs and outputs are checked for change/overwrite, and
the receipt is suitable as an explicit supplemental member of the authorized
archive.

## Negative coverage

Automated tests prove that:

- a complete, signed fixture is projected to all nine controls and reaches
  `APPROVAL_COLLECTION` only after persisted re-evaluation;
- omitting any required control refuses both outputs;
- substituting TLS evidence from another target refuses both outputs;
- rerunning against existing outputs cannot overwrite the prior authorization
  or receipt.

## Verification performed

```text
backend pytest: 1208 passed, 19 skipped, 3 warnings
production authorization suite: 107 passed
production operations config suite: 40 passed
production baseline: 25 PASS / 0 BLOCK
mypy: 81 errors <= baseline ceiling 82
Ruff: PASS
Python compile: PASS
GA CLI import/help: PASS
JSON/YAML parse: PASS
Bash syntax: PASS
git diff check: PASS
```

## Remaining external execution

The organization must still create the signed final tag/images, run every
collector against the real production target, obtain the independent security
assessment and supply the real request/policy. Only then can this assembler
produce the actual approval base. Campaign freeze, four independent human
decisions, finalization, archival and out-of-band digest publication remain
external release actions.
