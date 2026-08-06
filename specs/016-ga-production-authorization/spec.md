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
| PGA-01 | Production has a bundled TLS 1.2/1.3 edge; only 443 is public; HTTP/object/admin/monitoring ports are loopback or internal. Release-bound live collectors verify the exact target, commit and immutable images from an external vantage, retain full nmap results, CNI identity and NetworkPolicy specs, and exercise trusted/untrusted ingress plus allowed/denied egress with a reachable control destination. | `docker-compose.prod-tls.yml`; production baseline report; `duckdock-ga-tls-probe-v2`; `duckdock-ga-network-evidence-v2`. |
| PGA-02 | Secrets are external/SOPS managed, no plaintext source of truth remains, and target rotation is exercised without reading secret values. A content-addressed exact-principal policy separates provider and independent-verifier identities/keys; raw signed receipts prove provider version rotation/old-version disable plus old-version rejection/new-version acceptance. Metadata-only Kubernetes snapshots prove Secret resourceVersion change, Deployment generation advance, full backend/worker/beat readiness and complete Pod UID replacement. | `duckdock-ga-secrets-evidence-v2`, `duckdock-ga-secrets-trust-policy-v1`, signed provider rotation receipt and independently signed verification receipt. |
| PGA-03 | Prometheus routes to Alertmanager, delivery failure alerts exist, and an active target exercise proves firing/resolved delivery to at least two distinct channels/receivers plus human acknowledgement for a named on-call schedule. Delivery service and on-call identities use different keys authorized by a content-addressed exact-principal policy; the GA gate re-verifies all three raw OpenSSH-signed receipts, identical firing/resolved target sets and active/inactive API observations. | Repo config plus `duckdock-ga-alerting-evidence-v2`, `duckdock-ga-alerting-trust-policy-v1`, two signed delivery receipts and one signed on-call acknowledgement. |
| PGA-04 | DB, Git repo and object backups are age encrypted, signed, uploaded offsite and restored under RPO/RTO. The production gate independently verifies the original manifest OpenSSH signature and exact release commit. | Secure bundle manifest/signature, allowed-signers identity and destructive target restore receipt. |
| PGA-05 | A portable HA reference runs three backend/frontend/worker replicas across fault domains with PDB/HPA/default-deny policy; a disruptive target collector verifies exact context/images, drains every worker in one Beat-hosting zone under continuous public HTTPS probes, restores/rebalances the zone, binds the target network evidence, and requires an Operations-policy signature over managed state-service/RWX failover receipts. | Kustomize baseline plus `duckdock-kubernetes-ha-failover-v2` and `duckdock-ga-state-services-failover-v1`. |
| PGA-06 | A 900-second-or-longer capacity gate sustains the declared floor, materializes at least 50,000 runs, and proves post-growth timeline p95 through the real target HTTPS endpoint for the exact target, commit and immutable images. | `duckdock-target-capacity-gate-v2` JSON. |
| PGA-07 | An assessor independent of implementation tests the exact commit/images; no Critical/High remains. | Independent report bound by SHA-256. |
| PGA-08 | Product, Architecture, Security and Operations use four distinct OpenSSH identities and distinct public keys, each authorized for exactly that role by an out-of-band, content-addressed release-authority policy and one shared trust store, to approve the exact release/target/evidence/policy digest after evidence completion. Each signature also covers role, identity, decision and approval time. | `duckdock-ga-approval-policy-v1`, shared allowed-signers digest and four verified `duckdock-ga-approval-statement-v1` signatures. |
| PGA-09 | Final version is `2.0.0`, images use immutable `@sha256`, all RC gates are rerun through a release-bound target HTTPS readiness collector, and the production authorization result is `GA_AUTHORIZED`. | `duckdock-ga-target-readiness-v1` plus `duckdock-ga-production-authorization-v2` input/result bundle. |

## Truthful current boundary

PGA-01 through the repository portion of PGA-06 and the complete PGA-07/PGA-08
protocol are implemented and automated. PGA-08 now requires an out-of-band
release-authority policy, forbids per-approval trust-store overrides and binds
the policy digest into every signature. PGA-05 now also has a disruptive,
fail-closed target collector and v2 evidence parser; the target network path now also has a
fail-closed v2 collector and a GA parser that revalidates raw nmap, CNI, probe identity,
NetworkPolicy and connection results rather than trusting summary booleans. PGA-02 now has a
metadata-only target collector and v2 parser that reject replayed paths, secret-material fields,
provider/verifier identity or key reuse, forged receipt projections, and incomplete workload
rollouts. PGA-03 now has an
active target collector and v2 parser that separate delivery-service and human on-call keys,
reject replayed receipt paths, and re-verify all raw signatures. None of these target collectors
has been executed against a customer target. The local reference includes a real disposable
four-node kind rehearsal: restricted images are deployed across three simulated
zones, the Beat-hosting node is tainted/drained, stateless replicas recover in
the two surviving zones under continuous API probes, and a revision-aware
rolling rebalance restores three-zone coverage. Its report is deliberately
`local-rehearsal`: single-node state dependencies, emptyDir repositories and
unproven CNI enforcement remain false. A local capacity or HA result can prove
the engineering baseline, but not the customer's actual network, alert delivery/on-call, datastore or
fault domains. PGA-02/PGA-03/PGA-04/PGA-05 target receipts, PGA-07 independent
execution and PGA-08 human signatures cannot be fabricated by the project
agent; the final gate intentionally blocks without them.

The authoritative workflow is
[`docs/ga-production-authorization.zh-CN.md`](../../docs/ga-production-authorization.zh-CN.md).
