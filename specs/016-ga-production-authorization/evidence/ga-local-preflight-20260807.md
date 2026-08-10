# GA local preflight handoff — 2026-08-07

## Scope

This evidence records the first complete execution of
`scripts/run_ga_local_preflight.py`. The receipt is a local, non-authorizing
handoff. It does not assert target-production readiness, independent security
approval, organizational approval or GA publication authorization.

## Bound source

- Branch: `codex/release-2.0-rc1`
- Commit: `b50ea6532cf26067e9c05a2a84292c4f46d8652d`
- Git tree: `903bb28d3ea930cdfdeaf05bb01a817e145e5994`
- Version: `2.0.0-rc.1`
- Profile: `integrated`

The preflight refused to start until the worktree was clean. Its output root
was new and outside the repository.

## Result

- Receipt schema: `duckdock-ga-local-preflight-v1`
- Status: `PASS`
- Planned/executed checks: `73/73`
- Pass/block: `73/0`
- Content-addressed subsidiary artifacts: `74`
- Receipt SHA-256:
  `df65b6103dffb4811bde094e6188d367e951d80b332951716849d29613a7cd9c`
- External requirements: `6 PENDING_EXTERNAL`
- Next action: `collect_real_release_target_security_and_approval_evidence`

The repository profile passed dependency audit, Ruff, mypy ratchet, compile,
backend tests with coverage, frozen OpenAPI, frontend audit/lint/test/build,
development and production Compose validation, the production repository
baseline, GA authorization contract lint, every GA JSON template and production
script syntax. The integrated profile then started/reused the complete local
stack and passed health, Alembic, Playwright and live Langfuse compatibility
checks.

Selected underlying results:

- Backend: `1253 passed, 19 skipped`; gated coverage `81.04%` (`>=65%`).
- Frontend: `56 passed`; lint/build PASS.
- Playwright: `3/3` PASS.
- Langfuse v4 compatibility: `60/60` PASS against server 4.1.0.
- Production repository baseline: `36/36` PASS.
- mypy ratchet: `81 <= 82`.
- Python/frontend dependency audits: no known vulnerabilities at configured
  thresholds.

## Receipt hardening

The initial self-run revealed that Playwright-created files inherited 0755/0644
permissions. That receipt was retained but marked superseded. The preflight was
then changed to reject output symlinks, content-address every subsidiary file,
refuse receipt overwrite and recursively enforce directory/file modes 0700/0600.

The final run independently verified:

- receipt sidecar: PASS;
- all 74 artifact path/digest/size entries: PASS;
- files not mode 0600: `0`;
- directories not mode 0700: `0`;
- bounded token/password pattern matches: `0`.

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

Therefore `PASS` means that locally executable preflight work is reproducibly
green. It does not change the production authorization state to
`APPROVAL_COLLECTION` or `GA_AUTHORIZED`.
