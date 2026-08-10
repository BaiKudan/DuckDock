# Feature Specification: DuckDock Handover 2.0

**Status**: Completed
**Epic**: E06
**Gate**: G5 Handover RC
**Baseline**: DuckDock 2.0 revisions 0058/0059 and the existing v1 handover workflow

## 1. Goal

Upgrade the existing mutable handover workflow into an evidence-driven control-plane workflow without replacing or coupling it to a Runtime. A handover must freeze a reproducible point-in-time view of the governed Agent, its production release, package dependency graph, recent runs, evaluation baseline, risk, ownership, receiver permissions and runbook references. Open readiness gaps become explicit obligations. Acceptance and the outbound package must bind to that exact snapshot.

## 2. Compatibility boundary

- Existing `/api/v1/handovers/*` case, item, approval, execution receipt and verification APIs remain compatible.
- New governance resources live under `/api/v2` and reference an existing `HandoverCase`.
- The snapshot is metadata-only. Prompt/output bodies, secrets, raw traces and provider credentials stay in their owning systems.
- Existing MinIO evidence download authorization remains in force.
- A signed package uses an existing Namespace Ed25519 public-key trust record. DuckDock verifies a detached signature but never receives or stores the private key.

## 3. Acceptance slices

| Slice | Result definition |
|---|---|
| HO-01 | An append-only, idempotent evidence snapshot freezes a case, subject, receiver/fallback, assets, ownership, exact production deployment/package/release evidence, recent run summaries, Eval baseline, risk, permission summary and runbook references. |
| HO-02 | The snapshot contains a deterministic dependency graph with canonical node/edge ordering and a reproducible SHA-256 digest. Cross-Namespace references fail closed. |
| HO-03 | Deterministic readiness checks produce typed PASS/BLOCK results. Missing receiver access, owner/fallback, production version, runbook or required high-risk/sensitive evidence becomes an explicit obligation. |
| HO-04 | Obligations are immutable requirements with append-only fulfilment/failure receipts. Required-evidence obligations cannot be fulfilled without case-owned evidence. |
| HO-05 | Receiver acceptance is bound to one exact snapshot and its resolved obligations. A failed action or failed obligation requires explicit, immutable acknowledgement; unresolved blockers fail closed. |
| HO-06 | The outbound v2 package is deterministic, redacted, stored in MinIO, and contains the exact snapshot, readiness, obligations and acceptance. An active Namespace Ed25519 key signs the canonical manifest; DuckDock verifies the signature before persisting the detached attestation. |
| HO-07 | `/api/v2` management APIs and the existing handover detail UI expose snapshot graph/readiness, obligations, acceptance and signed package evidence without revealing secrets or raw provider content. |
| HO-08 | Real local MySQL/MinIO/Hermes-derived production evidence and browser validation complete the G5 technical lane; the IdP disable→credential revoke→handover trigger is completed together with E07 Identity/Security. |

## 4. State and invariants

1. A snapshot is immutable after insert. Re-snapshot creates the next sequence and never edits an earlier snapshot.
2. Snapshot idempotency keys are Namespace-scoped and payload-conflict detecting.
3. Canonical JSON uses sorted keys and compact separators; graph node and edge arrays are sorted by stable keys before hashing.
4. Every snapshot, obligation, receipt, acceptance and signed package belongs to the same Namespace and handover case.
5. An obligation may receive one terminal receipt. Replay with the same idempotency key and same payload returns the same row; conflicting replay is rejected.
6. Acceptance requires the assigned receiver (or an administrator acting under the existing handover authority), an active receiver identity, no unresolved blocking obligation and explicit failed-state acknowledgement when applicable.
7. A signed package can only be created after acceptance. Its manifest pins the snapshot digest, acceptance digest and terminal obligation receipt digests.
8. Signature verification uses the trusted public key only. Revoked/cross-Namespace keys and invalid signatures fail closed.

## 5. Evidence boundaries

Allowed snapshot and package fields are identifiers, versions, digests, statuses, bounded counts/timestamps, permission keys, risk classifications and allowlisted runbook references. The following never enter the snapshot/package/Audit/Outbox payloads:

- prompt, model output, raw trace/span or artifact body;
- access token, API key, private signing key or encrypted credential;
- unbounded provider metadata;
- free-form execution content beyond the existing sanitized handover summaries.

## 6. G5 technical exit

G5 technical acceptance requires HO-01 through HO-07 plus a real development-environment scenario that uses the existing Hermes deployment/release evidence, produces and independently verifies an Ed25519-signed package, confirms MinIO readback digest, exercises a blocked readiness/obligation lane, and verifies the UI with no console errors. HO-08's directory/offboarding automation remains the E06/E07 integration seam and closes only after E07 provides a real or staging-equivalent identity lifecycle event.

All eight slices are complete. Reproducible evidence is recorded in [handover-2-g5-20260804.md](evidence/handover-2-g5-20260804.md).
