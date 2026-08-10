# Campaign-bound Kubernetes target deployment gate — 2026-08-07

## Outcome

Repository-side gate: **PASS**. Real target execution and DuckDock 2.0 GA authorization:
**PENDING_EXTERNAL / NO-GO**.

The Kubernetes HA deployer is now part of the signed GA execution campaign rather
than a standalone operational helper. A production mutation is refused unless all
of the following resolve to one exact release and target:

- execution campaign v5 and its Security/Operations dual authorization;
- signed release provenance and immutable backend/frontend image digests;
- signed target cluster UID/principal and least-privilege access receipt;
- three or more declared target fault domains;
- exact Namespace, runtime/TLS Secret names, RWX claim/class/size and approved
  egress CIDRs;
- an Operations-signed `target_deployment` phase action under the campaign change
  request;
- a fresh live preflight and content-addressed target mutation confirmation.

The campaign reserves five target-bundle files, two phase-start files, preflight
receipt/sidecar and deployment receipt/sidecar. Closure v5 independently verifies
the completed deployment and includes it in the 102 external artifacts. Portable
archive verification reconstructs the five-file bundle and receipt pairs from
content-addressed members without reading original host paths. Physical paths are
not treated as security identity; campaign IDs, SHA-256 digests, signed projections,
release coordinates and cluster identity remain authoritative.

## Fail-closed behavior covered

- wrong release image, source commit, target scope, context, UID or principal;
- missing/tampered release provenance or cluster-access signature;
- wrong campaign output path at execution time;
- stale preflight or live target/RBAC/topology drift;
- phase action from another change request or mismatched authorization;
- preflight substitution after deployment;
- incomplete migration/application rollout and tampered workload projection;
- missing, substituted or non-portable bundle/receipt archive members.

Deployment success remains `TARGET_HA_DEPLOYMENT_COMPLETED_NOT_GA_AUTHORIZED`.
Secret values/rotation, external TLS and NetworkPolicy enforcement, managed
MySQL/Redis/S3/RWX redundancy, sustained target capacity/data growth, recovery and
on-call exercise, target fault-domain survival, independent security assessment
and four-role approval remain external requirements.

## Verification

- Kubernetes bundle/deployment tests: 16 passed.
- GA production authorization tests: 136 passed (including closure → four-role
  authorization → deterministic archive → isolated archive verification →
  publication authorization).
- Production repository baseline: 39/39 PASS.
- Full backend suite: 1279 passed, 19 skipped, 0 failed (three dependency
  deprecation warnings).
- Frontend: 56 tests passed; ESLint and production build passed.
- Ruff, compileall, `git diff --check`, example JSON parsing and the production
  baseline completed without blockers.
