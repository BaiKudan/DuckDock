# GA local preflight handoff for `522f5a1` — 2026-08-07

## Scope

This evidence records a complete rerun of the repository's one-command GA
local preflight after the campaign-bound Kubernetes target deployment gate was
merged into the release candidate. It is a local, non-authorizing handoff. It
does not assert target-production readiness, independent security approval,
organizational approval or GA publication authorization.

## Bound source

- Branch: `codex/release-2.0-rc1`
- Commit: `522f5a18b09a4e5168214a583ce146c6dbea7e33`
- Git tree: `ce1adcbff1b06de3d1fb9994ec531a92ec88ca74`
- Version: `2.0.0-rc.1`
- Profile: `integrated`
- Execution window: `2026-08-07T08:59:37Z` to `2026-08-07T09:07:52Z`

The preflight started only after confirming a clean worktree. Its output root
was newly created outside the repository.

## Result

- Receipt schema: `duckdock-ga-local-preflight-v1`
- Status: `PASS`
- Planned/executed checks: `73/73`
- Pass/block: `73/0`
- Content-addressed subsidiary artifacts: `74`
- Receipt SHA-256:
  `e627babb100c122dd766ca8329152de2003ec09eefae5a5a997ba3e5e3185f85`
- External requirements: `6 PENDING_EXTERNAL`
- Next action: `collect_real_release_target_security_and_approval_evidence`

The repository profile passed dependency audit, Ruff, mypy ratchet, compile,
backend tests with coverage, frozen OpenAPI, frontend audit/lint/test/build,
development and production Compose validation, the production repository
baseline, GA authorization contract lint, every GA JSON template and
production script syntax. The integrated profile then started or reused the
complete local stack and passed health, Alembic, Playwright and live Langfuse
compatibility checks.

Selected underlying results:

- Backend: `1279 passed, 19 skipped`; gated coverage `81.04%` (`>=65%`).
- Frontend: `56 passed`; lint/build PASS.
- Playwright: `3/3` PASS.
- Langfuse v4 compatibility: `60/60` PASS against server `4.1.0` with SDK
  `4.14.2`.
- Production repository baseline: `39/39` PASS.
- mypy ratchet: `81 <= 82`.
- Python and frontend dependency audits: PASS at the configured thresholds.

## Independent receipt verification

After the preflight process exited, a separate verification pass reopened the
persisted output and confirmed:

- receipt sidecar: PASS;
- source commit, tree, branch and version: exact match;
- authorization scope begins with `non-authorizing local preflight`;
- all `74/74` artifact path/digest/size entries: PASS;
- files not mode `0600`: `0`;
- directories not mode `0700`: `0`;
- symbolic links: `0`;
- all six external requirement statuses: `PENDING_EXTERNAL`.

Environment-specific log paths and provider object IDs are intentionally not
committed. The private local receipt retains the complete command, duration,
exit code and log digest for each check.

## Deliberately unclosed external requirements

The PASS receipt still records all of these as `PENDING_EXTERNAL`:

1. signed `v2.0.0` tag, final CI, immutable images, SLSA, SBOM and scans;
2. target TLS, Secrets, network isolation, alert/on-call and backup controls;
3. sustained target load, datastore growth and cleanup receipts;
4. target fault-domain and managed state-service failover evidence;
5. Security-authorized independent assessment, retest and deletion proof;
6. distinct Product, Architecture, Security and Operations signatures.

Therefore `PASS` means that the complete locally executable preflight is green
for commit `522f5a1`. It does not change the production authorization state to
`APPROVAL_COLLECTION`, `GA_AUTHORIZED` or `GA_PUBLICATION_AUTHORIZED`.
