# DuckDock 2.0 Production GA Authorization

**Status**: Technical implementation complete; target authorization pending
**Date**: 2026-08-06
**Depends on**: `015-ga-candidate`

## Goal

Turn the RC technical candidate into a target-specific, content-addressed and
cryptographically approved production release. The application readiness API
remains read-only evidence; it must never silently imply organizational or
deployment authorization.

## Acceptance criteria

| ID | Requirement | Completion evidence |
|---|---|---|
| PGA-01 | Production has a bundled TLS 1.2/1.3 edge; only 443 is public; HTTP/object/admin/monitoring ports are loopback or internal. Release-authority policies fix exact external TLS/network probe identities and keys, probe/vantage IDs and globally routable source CIDRs. The TLS gate re-verifies signed raw certificate fingerprints, validity, cipher, HTTP/HSTS observations and bounded OpenSSL legacy-protocol output. The network gate re-verifies a signed raw report bound to an approved kube context, Namespace and CNI, then recomputes full nmap results, CNI identity and NetworkPolicy specs and trusted/untrusted ingress plus allowed/denied egress with a reachable control destination instead of trusting wrapper booleans. | `docker-compose.prod-tls.yml`; production baseline report; `duckdock-ga-tls-evidence-v3`, `duckdock-ga-tls-trust-policy-v1`, signed `duckdock-ga-tls-probe-v3`; `duckdock-ga-network-evidence-v3`, `duckdock-ga-network-trust-policy-v1`, signed `duckdock-ga-network-probe-v3`. |
| PGA-02 | Secrets are external/SOPS managed, no plaintext source of truth remains, and target rotation is exercised without reading secret values. A content-addressed exact-principal policy separates provider and independent-verifier identities/keys; raw signed receipts prove provider version rotation/old-version disable plus old-version rejection/new-version acceptance. Metadata-only Kubernetes snapshots prove Secret resourceVersion change, Deployment generation advance, full backend/worker/beat readiness and complete Pod UID replacement. | `duckdock-ga-secrets-evidence-v2`, `duckdock-ga-secrets-trust-policy-v1`, signed provider rotation receipt and independently signed verification receipt. |
| PGA-03 | Prometheus routes to Alertmanager, delivery failure alerts exist, and an active target exercise proves firing/resolved delivery to at least two distinct channels/receivers plus human acknowledgement for a named on-call schedule. Delivery service and on-call identities use different keys authorized by a content-addressed exact-principal policy; the GA gate re-verifies all three raw OpenSSH-signed receipts, identical firing/resolved target sets and active/inactive API observations. | Repo config plus `duckdock-ga-alerting-evidence-v2`, `duckdock-ga-alerting-trust-policy-v1`, two signed delivery receipts and one signed on-call acknowledgement. |
| PGA-04 | DB, Git repo and object backups are age encrypted, signed, uploaded offsite and destructively restored only into an explicitly acknowledged non-production target. The gate verifies the original release-bound manifest, then requires separate exact-principal signatures from the storage service, restore executor and independent verifier. Offsite object versions/digests/sizes must match the manifest and remain Object-Locked for at least 30 days; restore stages retain exit-code/log digests; MySQL rows, object inventory, Git refs and service readiness are independently verified. RPO/RTO are recomputed from the signed recovery-point/failure/verification timeline. | `duckdock-ga-recovery-evidence-v2`, `duckdock-ga-recovery-trust-policy-v1`, signed backup manifest plus storage, restore-execution and independent-verification receipts. |
| PGA-05 | A portable HA reference runs three backend/frontend/worker replicas across fault domains with PDB/HPA/default-deny policy; a disruptive target collector verifies exact context/images, drains every worker in one Beat-hosting zone under continuous public HTTPS probes, restores/rebalances the zone and binds target network evidence. Managed MySQL/Redis/object/RWX evidence requires a content-addressed policy, a provider-signed automatic cross-domain failover event, a distinct verifier-signed pre/post integrity and write/read-back result for every service, and an Operations-policy signature over the assembled wrapper. | Kustomize baseline; `duckdock-kubernetes-ha-failover-v2`; `duckdock-ga-state-services-failover-v2`; provider/verifier receipts and state-services trust policy. |
| PGA-06 | A 900-second-or-longer capacity gate sustains the declared floor through the real target HTTPS endpoint and materializes at least 50,000 runs for the exact target, commit and immutable images. A content-addressed exact-principal policy separates load executor, storage observer and cleanup verifier identities/keys. The GA gate re-verifies their three raw OpenSSH-signed reports, requires HTTP materialization to equal actual AgentRun delta and run-tag rows, checks Audit/Outbox deltas, database-byte growth, pending outbox and replica lag, recomputes post-growth timeline p95, then proves Namespace deletion, credential revocation and zero exercise-scoped residue. | `duckdock-target-capacity-gate-v3`, `duckdock-ga-capacity-trust-policy-v1`, signed v2 load report, signed growth receipt and independently signed cleanup receipt. |
| PGA-07 | An assessor independent of implementation and all internal approvers tests the exact target/contract/commit/images. The release authority pre-authorizes the provider and exact assessor identity/key; the gate re-verifies the signed raw JSON, its content-addressed PDF, every finding timeline and recomputed severity counts. No Critical/High remains open. | `duckdock-ga-independent-security-evidence-v2`, signed `duckdock-ga-security-assessment-report-v1` and linked PDF. |
| PGA-08 | Product, Architecture, Security and Operations use four distinct OpenSSH identities and distinct public keys, each authorized for exactly that role by an out-of-band, content-addressed release-authority policy and one shared trust store. External assessor identities/keys are also exact-policy-bound and disjoint from all approvers. A fail-closed pre-approval evaluation must first report `campaign_stage=APPROVAL_COLLECTION` and `evidence_ready_for_approval=true`; missing, stale or cross-release evidence can never be reported as merely awaiting approvals. The four roles then approve the exact release/target/evidence/policy digest after evidence completion; each signature covers role, identity, decision and approval time. | `duckdock-ga-approval-policy-v2`, `--require-evidence-ready` result, shared allowed-signers digest and four verified `duckdock-ga-approval-statement-v1` signatures. |
| PGA-09 | Final version is `2.0.0`, images use immutable `@sha256`, all RC gates are rerun through a release-bound target HTTPS readiness collector, and the production authorization result is `GA_AUTHORIZED`. | `duckdock-ga-target-readiness-v1` plus `duckdock-ga-production-authorization-v2` input/result bundle. |

## Truthful current boundary

PGA-01 through the repository portion of PGA-06 and the complete PGA-07/PGA-08
protocol are implemented and automated. PGA-07 now has a fail-closed v2 collector and parser:
the organization policy fixes external provider identities/keys, the assessor signs a raw finding-level
JSON that content-addresses the PDF, and the gate reopens both instead of trusting projections.
PGA-08 requires the same out-of-band release-authority policy, forbids per-approval/per-assessment
trust-store overrides and binds the policy digest into every signature. PGA-05 now also has a disruptive,
fail-closed target collector and v2 evidence parser; the target network path now also has a
fail-closed v2 collector and a GA parser that revalidates raw nmap, CNI, probe identity,
NetworkPolicy and connection results rather than trusting summary booleans. PGA-01 TLS evidence
now also uses a signed v3 raw probe plus a content-addressed external-vantage
policy; the final gate recomputes certificate lifetime, protocol/HSTS results and legacy rejection,
and rejects wrapper projection edits or unapproved source networks. PGA-02 now has a
metadata-only target collector and v2 parser that reject replayed paths, secret-material fields,
provider/verifier identity or key reuse, forged receipt projections, and incomplete workload
rollouts. PGA-03 now has an
active target collector and v2 parser that separate delivery-service and human on-call keys,
reject replayed receipt paths, and re-verify all raw signatures. PGA-04 now also has a target v2 collector and parser
that replace manually asserted restore/RPO/RTO fields with three role-separated signed receipts,
manifest-to-offsite version matching and a recomputed recovery timeline. PGA-06 now uses a v3
collector/parser that replaces trusted HTTP 201 counts and a manual DBA note with three role-separated
signed source reports, actual MySQL deltas/bytes/backlog/lag and independently verified cleanup.
The final authorization evaluator now exposes one authoritative campaign state machine:
`FOUNDATION`, `EVIDENCE_COLLECTION`, `APPROVAL_COLLECTION`, then `AUTHORIZED`. Its
`--require-evidence-ready` mode succeeds only when every non-approval check passes, so an invalid
third-party assessment, missing target report, stale probe, cross-release artifact or changed evidence
digest cannot be mislabeled as merely waiting for organizational signatures.
None of these target collectors
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
