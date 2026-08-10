# DuckDock 2.0 E05 Release Control Specification

> Status: Completed — G4 technical evidence recorded on 2026-08-04
>
> Owner: Release Architecture
>
> Scope: S7-S8 / M4 / G4
>
> Dependencies: E03 Eval Hub, E04 Package Registry v2

## 1. Goal

E05 turns immutable PackageVersion and evaluation evidence into an explainable,
environment-aware release workflow:

```text
PackageVersion + Deployment snapshot + Eval evidence
  -> ReleaseCandidate
  -> versioned Policy evaluation (shadow / warn / enforce)
  -> independent approval / bounded exception
  -> Promotion request
  -> Runtime-authenticated receipt
  -> Canary observation
  -> active Environment release or rollback
```

DuckDock remains the governance control plane. It does not pretend that a
database state transition deployed code. A promotion only succeeds after the
target Runtime returns an authenticated receipt that reports the exact
PackageVersion, deployment revision and configuration digest.

## 2. Non-goals and trust boundary

- Do not implement a general deployment orchestrator or embed Runtime-specific
  SDKs in the release domain.
- Do not read a Namespace's "latest" evaluation, package or deployment as
  release evidence. Every subject and evidence item is pinned by public ID and
  digest.
- Do not persist prompts, messages, tool arguments/results, SBOM bodies or
  Runtime secrets in release tables, Audit or Outbox.
- A shadow or warn decision never silently becomes enforce. Policy mode is an
  immutable property of an exact PolicyVersion.
- Operator acknowledgement is not a Runtime receipt. Runtime receipts are
  accepted only from credentials bound server-side to the target Runtime.
- Automatic production activation, Runtime delivery adapters and destructive
  rollback are disabled until their exact request/receipt protocol is present.

## 3. Acceptance slices

| Slice | Result definition |
|---|---|
| RC-01 | Namespace-scoped `ReleaseEnvironment` and immutable `ReleaseCandidate` bind an exact verified PackageVersion, registered Deployment snapshot, target Environment and optional exact baseline candidate. Mutable `latest` selectors are rejected. |
| RC-02 | `ReleasePolicy` has append-only versions. Each version pins `SHADOW`, `WARN` or `ENFORCE`, target Environment and a bounded typed rule set with a canonical digest. |
| RC-03 | Policy evaluation produces one immutable `PolicyDecision` and ordered per-rule results. Package signature/SBOM, signing-key status, Eval Gate, risk tier, tool additions, capability/permission expansion, vulnerability severity and rollback target are explainable by exact evidence refs/digests. Missing evidence fails closed at the rule level. |
| RC-04 | Shadow permits while recording a would-block outcome; warn permits with explicit warning; enforce blocks. Re-evaluation is idempotent for the same candidate/policy/evidence snapshot and conflicts when an idempotency key is reused for different evidence. Audit/Outbox contain only bounded summaries. |
| RC-05 | Candidate approval and time-bounded Policy exceptions are immutable and independently authorized. Production approval enforces four-eyes; rejection is terminal for the exact candidate; expired exceptions are ignored. |
| RC-06 | Promotion follows an explicit DEV -> STAGING -> CANARY -> PRODUCTION graph, is idempotent, consumes an allowed PolicyDecision plus approvals, and emits a target-specific dispatch request without claiming success. |
| RC-07 | A target Runtime returns an authenticated, idempotent `DeploymentReceipt` containing the actually observed PackageVersion, Deployment revision and configuration digest. Mismatch or failure is terminal and cannot update the Environment active release pointer. |
| RC-08 | Canary analysis uses bounded metadata-only AgentRun aggregates for the exact deployed revision. Failure creates an idempotent rollback to the last successful Environment release; success permits full promotion. Request, receipt, active-pointer change and rollback are audited and visible in UI. |

## 4. Domain invariants

### 4.1 Environment

- Environment identity is unique by `(namespace_id, name)`.
- Kind is one of `DEVELOPMENT`, `TEST`, `STAGING`, `CANARY`, `PRODUCTION`.
- `promotion_order` is unique per Namespace and makes the forward graph
  deterministic. A skip requires an explicit, unexpired exception.
- Protected Environments define minimum approvals and whether a canary is
  mandatory.

### 4.2 ReleaseCandidate

- Candidate identity includes exact PackageVersion, Deployment snapshot,
  target Environment, optional source/baseline candidate and canonical digest.
- PackageVersion and Deployment must belong to the same Namespace and Agent;
  the Deployment must pin that PackageVersion.
- Candidate creation freezes a REGISTERED Deployment through an explicit
  release binding. Candidate fields are never updated after creation; workflow
  state is derived from immutable decisions, approvals, promotions and
  receipts.
- A baseline candidate, when supplied, must be the active release of an earlier
  Environment and the same Agent Package.

### 4.3 Policy rules

Initial rule types:

- `PACKAGE_EVIDENCE_VERIFIED`
- `SIGNING_KEY_ACTIVE`
- `EVALUATION_GATE_PASS`
- `RISK_TIER_ALLOWED`
- `MAX_TOOL_ADDITIONS`
- `FORBID_CAPABILITY_EXPANSION`
- `MAX_VULNERABILITY_SEVERITY`
- `ROLLBACK_TARGET_REQUIRED`

Each result records rule ID/type, `PASS|FAIL|NOT_APPLICABLE`, reason code,
bounded metrics, evidence kind/ref/digest and observation time. Rule result
rows never contain SBOM documents or other raw content.

Vulnerability severity is derived transiently from the exact content-addressed
SBOM. CycloneDX ratings and SPDX package annotations/external references are
normalized conservatively. Unknown declared severities fail closed when the
rule is enabled. Only counts, maximum severity and the SBOM digest are stored.

### 4.4 Policy modes

| Raw rule outcome | SHADOW | WARN | ENFORCE |
|---|---|---|---|
| all pass | ALLOW | ALLOW | ALLOW |
| one or more fail | ALLOW (`would_block=true`) | WARN | BLOCK |

The raw verdict and enforcement outcome are separate fields so a future mode
change cannot reinterpret historical decisions.

### 4.5 Promotion and receipt

- A promotion references exactly one Candidate and exact PolicyDecision.
- `ENFORCE/BLOCK` can never dispatch. `WARN` requires explicit acknowledgement
  on the promotion request. `SHADOW` remains observable but does not block.
- Dispatch payload contains no credential or content. It contains immutable
  refs/digests and a stable dispatch ID.
- Receipt authentication derives Namespace and Runtime from the credential;
  request body identity is never trusted.
- Environment active release changes only after a matching successful receipt
  and, for canary, a passing observation.

### 4.6 Canary and rollback

- Canary policy pins minimum completed Runs, maximum failure rate, maximum
  untrusted rate and observation window.
- Only AgentRuns linked to the exact Deployment and falling inside the window
  are counted.
- Insufficient samples are `INCONCLUSIVE` and fail closed.
- Rollback targets the previous successful receipt, never a mutable "latest".
- A rollback is complete only after the Runtime returns a matching receipt.

## 5. API surface

Management endpoints use Namespace membership/RBAC. Runtime receipt endpoints
use Reporter credentials with an explicit `release.receipt` scope.

```text
POST/GET  /api/v2/release-environments
POST/GET  /api/v2/release-policies
POST      /api/v2/release-policies/{id}/versions
POST/GET  /api/v2/release-candidates
POST      /api/v2/release-candidates/{id}/policy-evaluations
POST      /api/v2/release-candidates/{id}/approvals
POST      /api/v2/release-policy-exceptions
POST/GET  /api/v2/promotions
POST      /api/v2/reporter/promotion-receipts
POST      /api/v2/promotions/{id}/canary-evaluations
POST      /api/v2/promotions/{id}/rollback
```

All mutating endpoints require an `Idempotency-Key` or a body idempotency key,
depending on whether a Runtime or human client owns the request envelope.

## 6. Security and privacy acceptance

- Cross-Namespace references return not-found or a typed tenant mismatch and
  create no release record.
- Candidate, decision, approval, exception, promotion, receipt, canary and
  rollback state changes write low-sensitivity Audit plus transactional Outbox.
- Audit/Outbox allowlists exclude policy free-form notes, exception reason,
  SBOM body, manifest body, Runtime response body and all model/tool content.
- Policy evaluation is bounded to 100 rules, 2 MiB SBOM and 1000 normalized
  vulnerability entries; oversized or malformed evidence fails closed.
- Decision p95 target is below 100 ms excluding object-store retrieval; the UI
  reports evidence retrieval latency separately.

## 7. G4 completion evidence

G4 requires all eight slices, migration upgrade and guarded downgrade evidence,
full backend/frontend regression, idempotency/race tests, a real MySQL/MinIO
lane, browser verification, and a real Hermes receipt/canary/rollback drill.
Policy shadow alone closes S7/M4a but does not close E05 or G4.

Recorded evidence: [E05 Release Control / G4](evidence/release-control-g4-20260804.md).
