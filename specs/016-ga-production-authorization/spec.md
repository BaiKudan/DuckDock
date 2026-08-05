# DuckDock 2.0 Production GA Authorization

**Status**: Technical implementation complete; target authorization pending
**Date**: 2026-08-05
**Depends on**: `015-ga-candidate`

## Goal

Turn the RC technical candidate into a target-specific, content-addressed and
cryptographically approved production release. The application readiness API
remains read-only evidence; it must never silently imply organizational or
deployment authorization.

## Acceptance criteria

| ID | Requirement | Completion evidence |
|---|---|---|
| PGA-01 | Production has a bundled TLS 1.2/1.3 edge; only 443 is public; HTTP/object/admin/monitoring ports are loopback or internal. The live probe binds the exact target, commit and immutable images. | `docker-compose.prod-tls.yml`; production baseline report; `duckdock-ga-tls-probe-v2`. |
| PGA-02 | Secrets are external/SOPS managed, no plaintext source of truth remains, and rotation is exercised. | Target secret-manager/rotation receipt. |
| PGA-03 | Prometheus routes to Alertmanager, delivery failure alerts exist, and firing/resolved reach named on-call. | Repo config plus target receiver receipts. |
| PGA-04 | DB, Git repo and object backups are age encrypted, signed, uploaded offsite and restored under RPO/RTO. | Secure bundle manifest/signature and destructive target restore receipt. |
| PGA-05 | A portable HA reference runs three backend/frontend/worker replicas across fault domains with PDB/HPA/default-deny policy; state services and RWX storage are HA. | Kustomize baseline plus target node/zone/Beat failover report. |
| PGA-06 | A 900-second-or-longer capacity gate sustains the declared floor, materializes at least 50,000 runs, and proves post-growth timeline p95 through the real target HTTPS endpoint for the exact target, commit and immutable images. | `duckdock-target-capacity-gate-v2` JSON. |
| PGA-07 | An assessor independent of implementation tests the exact commit/images; no Critical/High remains. | Independent report bound by SHA-256. |
| PGA-08 | Product, Architecture, Security and Operations use four distinct OpenSSH identities to approve the exact release/target/evidence digest after evidence completion. | Four verified `duckdock-ga` signatures. |
| PGA-09 | Final version is `2.0.0`, images use immutable `@sha256`, all RC gates are rerun through a release-bound target HTTPS readiness collector, and the production authorization result is `GA_AUTHORIZED`. | `duckdock-ga-target-readiness-v1` plus `duckdock-ga-production-authorization-v1` input/result bundle. |

## Truthful current boundary

PGA-01 through the repository portion of PGA-06 and the complete PGA-07/PGA-08
protocol are implemented and automated. PGA-05 now includes a real disposable
four-node kind rehearsal: restricted images are deployed across three simulated
zones, the Beat-hosting node is tainted/drained, stateless replicas recover in
the two surviving zones under continuous API probes, and a revision-aware
rolling rebalance restores three-zone coverage. Its report is deliberately
`local-rehearsal`: single-node state dependencies, emptyDir repositories and
unproven CNI enforcement remain false. A local capacity or HA result can prove
the engineering baseline, but not the customer's actual network, datastore or
fault domains. PGA-02/PGA-03/PGA-04/PGA-05 target receipts, PGA-07 independent
execution and PGA-08 human signatures cannot be fabricated by the project
agent; the final gate intentionally blocks without them.

The authoritative workflow is
[`docs/ga-production-authorization.zh-CN.md`](../../docs/ga-production-authorization.zh-CN.md).
