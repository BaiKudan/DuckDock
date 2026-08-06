# DuckDock 2.0 GA execution-campaign progress gate — 2026-08-07

## Scope and truth boundary

This record covers repository tooling for observing a partially executed GA
campaign before its all-or-nothing closure. A progress checkpoint cannot
authorize target mutation, assert that evidence passed, assemble preapproval,
collect human decisions or substitute for a third-party assessment.

## Implemented control

- `inspect_ga_execution_campaign.py` independently regenerates the persisted
  campaign and assembly request from their content-addressed request and trust
  topology inputs before inspecting external files.
- All 64 external artifact paths remain lexical, so a symlink at an expected
  path cannot disappear through premature path resolution. Regular-file type,
  non-empty and bounded size, JSON shape, common private-key markers and stable
  metadata during inspection are checked.
- Every discovered content, manifest, allowed-signers or signature reference
  must resolve to another planned artifact or an independently verified fixed
  campaign/topology input. Declared digests are compared and invalid referenced
  artifacts propagate back through the reference graph.
- Eleven external phases are classified using the immutable dependency graph.
  Window state distinguishes not-started, active and expired campaigns; partial
  or invalid closure outputs are quarantined, while a complete persisted
  closure is rerun through `verify_persisted_closure`.
- Optional checkpoints are immutable and must remain outside the exact evidence
  root. `--require-ready-for-closure` exits 2 until all external phases are
  merely artifact-ready; the existing closure/full evaluator remains mandatory.

## Verification performed

- Empty campaign: 64 missing artifacts, zero ready phases and exact
  `release_provenance` next action.
- Content-addressed reference to an unplanned path: rejected as invalid.
- Symlink at a planned path: retained lexically and rejected.
- Evidence-root replacement with a directory symlink: rejected during campaign
  regeneration; direct closure also rejects a symlink at any planned artifact.
- Fully materialized signed fixture: 64 valid artifacts, 11 artifact-ready
  external phases and `READY_FOR_CLOSURE_ATTEMPT` without a PASS claim.
- Pre-window and expired snapshots: correctly separated from active readiness.
- Immutable persisted checkpoint reopens byte-for-byte as the in-memory result.
- Formal closure fixture: independently recognized as `CAMPAIGN_CLOSED` with
  the exact closure SHA-256 and freeze as the next action.
- Full backend regression: 1224 passed, 19 skipped, 3 existing warnings.
- Production authorization module: 122 passed as part of the full regression.
- Production operations configuration tests: 41 passed.
- Frontend: 56 tests passed; lint and production build passed.
- Production repository baseline: 30/30 passed.
- Ruff/compile/YAML/JSON/Compose/OpenAPI: clean; mypy 81 errors remain below
  the frozen ceiling of 82.
- Locked backend and frontend dependency audits: no known vulnerabilities.

## Remaining external action

The release authority still has to provision the real production target and
role-separated identities, execute the external phases, obtain the independent
assessment, create all 64 planned files inside the bounded window and run the
original closure. These tests use signed fixtures and do not represent customer
or production evidence.
