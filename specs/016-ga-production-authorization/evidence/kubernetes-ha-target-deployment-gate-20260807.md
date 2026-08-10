# Kubernetes HA target deployment gate — 2026-08-07

## Outcome

DuckDock now has a fail-closed, machine-sequenced path from a verified HA target
bundle to a target deployment. The repository implementation and simulated
cluster regression passed. No real Kubernetes context was contacted and no
real target resource was created, changed or deleted during this validation.

- Source commit: `61e3ee20a8a67f67a52eb5a6b2d4e5cbef2542eb`
- Source tree: `4ee8203a81a894749c211a47b46387ad344999b5`
- Branch: `codex/release-2.0-rc1`
- Candidate version: `2.0.0-rc.1`
- Repository production baseline: 39 PASS / 0 BLOCK
- Bundle and deployment regression: 14 passed
- Deployment-specific regression: 11 passed
- Backend full regression after integration: 1277 passed / 19 skipped

The implementation emits
`duckdock-kubernetes-ha-target-preflight-v1` and
`duckdock-kubernetes-ha-target-deployment-v1` receipts. Their success states
explicitly end in `NOT_GA_AUTHORIZED`; they are operational audit records, not
substitutes for the signed GA evidence campaign.

## Read-only preflight boundary

Before target mutation, the preflight performs these checks against the exact
reviewed Kubernetes context:

- authenticated principal and `kube-system` UID equal the independently
  reviewed values;
- Kubernetes is at least 1.27;
- the target Namespace already exists with `restricted` enforce, audit and
  warn Pod Security labels;
- at least three Ready, schedulable nodes have distinct hostnames and span at
  least three zones; metrics-server reports every Ready node;
- the reviewed RWX StorageClass exists;
- runtime and TLS Secrets have the expected type and required key names; Secret
  values are neither read into a receipt nor recorded;
- ingress and monitoring Namespaces have the required labels;
- the deployment identity passes an exact allow/deny RBAC matrix, including
  denial of Secret mutation, RBAC mutation, impersonation and destructive
  workload/PVC actions;
- bootstrap, migration and applications each pass a real Kubernetes
  server-side dry-run.

The preflight receipt is content-addressed and immutable. Offline verification
remains possible after it ages, but target mutation requires an observation no
older than one hour.

## Mutation boundary and sequence

Deployment requires all of the following before its first write:

1. a valid, fresh preflight bound to the exact bundle directory;
2. a safe change-request identifier;
3. the exact confirmation
   `APPLY_DUCKDOCK_HA_TARGET:<context>:<namespace>:<bundle-receipt-sha256>`;
4. a complete live preflight rerun whose target projection exactly matches the
   saved preflight.

The executor then applies only this sequence:

1. bootstrap, followed by RWX PVC `Bound`;
2. the commit-named migration Job, followed by `Complete` and an image/commit
   result check;
3. applications, followed by backend, frontend, worker and beat rollout and
   image/commit/replica/generation checks.

A successful run creates
`TARGET_HA_DEPLOYMENT_COMPLETED_NOT_GA_AUTHORIZED`. Once the first apply request
begins, any exception creates
`TARGET_HA_DEPLOYMENT_INCOMPLETE_NOT_GA_AUTHORIZED` with the exact command prefix
and completed projections. The executor does not automatically roll back a
database migration; an incomplete receipt must enter change-incident handling.

## Verified negative paths

The simulated cluster tests reject:

- a cluster UID different from the reviewed target;
- fewer than three Ready zones;
- a missing `DATABASE_URL` runtime Secret key name;
- denial of a required deployment permission;
- use of a stale preflight for mutation while preserving offline verification;
- semantic preflight tampering even after recomputing its digest sidecar;
- an inexact content-addressed mutation confirmation before any runner call;
- application-phase failure after bootstrap and migration, while preserving an
  immutable incomplete receipt;
- a forged out-of-order workload projection in an incomplete receipt.

Ruff passed both the repository rules and the high-confidence
`S101,S110,S314` security selection. The CLI help path and the repository
production baseline also passed.

## Remaining mandatory external work

This gate deliberately keeps all of the following at `PENDING_EXTERNAL`:
release-image provenance; runtime Secret values and rotation; an external TLS
probe; NetworkPolicy enforcement; RWX redundancy; managed MySQL/Redis/S3 HA;
target capacity and data growth; backup restore and on-call delivery; a real
fault-domain exercise; independent security assessment; and Product,
Architecture, Security and Operations approvals.

Therefore the evidence changes the repository implementation verdict from
"manual target sequencing" to "machine-gated target sequencing". It does not
change the overall DuckDock 2.0 verdict: RC/controlled pilot is acceptable,
while public GA and production authorization remain blocked.
