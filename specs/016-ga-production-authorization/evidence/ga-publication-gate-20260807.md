# DuckDock 2.0 GA publication gate — 2026-08-07

## Scope and truth boundary

This record covers the repository-side transition from an independently
verified, closure-bound GA archive to a protected GitHub draft-release
publication. It does not claim that a real production campaign, external
assessment, four human approvals, GitHub Environment approval or public
`v2.0.0` Release has occurred.

## Implemented control

- `verify_ga_authorized_archive.py` now exposes release, target and final-tag
  build context only from the same isolated authorization/provenance members
  that passed its full evaluator. Its existing CLI v1 output remains stable.
- `authorize_ga_publication.py` has no legacy compatibility switch. It reruns
  portable archive verification using an independently supplied digest,
  requires `2.0.0`, the exact commit and immutable images, a named production
  `kubernetes-ha` target, the expected repository and final-tag CI workflow,
  and a publication time no more than 24 hours after canonical authorization.
- Success emits immutable `duckdock-ga-publication-authorization-v1` JSON and
  a filename-bound SHA-256 sidecar. The receipt explicitly says that it does
  not prove external publication.
- `.github/workflows/publish-ga.yml` runs only in `BaiKudan/DuckDock`, uses the
  `ga-production-publication` Environment, verifies the signed tag and exact
  draft assets, runs the publication authorizer, reconfirms the archived CI run
  through GitHub's API, uploads and byte-compares the receipt, then publishes
  that same draft. A retry accepts only an already valid exact receipt pair and
  never overwrites it.

## Verification performed

- YAML parsing: clean.
- Ruff and Python compilation for the changed scripts/tests: clean.
- Full backend regression: 1223 passed, 19 skipped, 3 existing warnings.
- Production authorization module: 121 passed as part of the full regression.
- Production operations configuration tests: 41 passed.
- Frontend: 56 tests passed; lint and production build passed.
- Production repository baseline: 29/29 passed.
- Ruff/compile/YAML/JSON/Compose/OpenAPI: clean; mypy 81 errors remains
  below the frozen ceiling of 82.
- Locked backend and frontend dependency audits: no known vulnerabilities.
- Publication stale/mismatched-context negative test: passed.
- Formal signed fixture: 64 external artifacts → 88 captured inputs → 133
  references → closure-bound v2 freeze → four distinct signatures →
  `GA_AUTHORIZED` → deterministic archive →
  `GA_AUTHORIZED_ARCHIVE_VERIFIED` → `GA_PUBLICATION_AUTHORIZED`: passed.

## Remaining external action

The repository owner must configure real required reviewers on the
`ga-production-publication` Environment, create the real signed tag and final
CI images, execute the target campaign and independent assessment, collect four
human approvals, publish the archive digest through an independent immutable
channel, create the exact draft assets and approve/run the publication
workflow. Until its post-transition API check observes `draft=false`, DuckDock
2.0 is not publicly GA.
