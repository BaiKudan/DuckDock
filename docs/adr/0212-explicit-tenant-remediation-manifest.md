# ADR-0212: Explicit per-ID tenant remediation manifest

**Status**: Accepted
**Date**: 2026-07-28

## Context

Foundation expand/backfill correctly refuses to infer historical Namespace from Membership, names, Provider labels,
URLs or JSON metadata. Some deployed databases have no typed evidence at all, so deterministic backfill cannot reach
the contract phase without an explicit governance decision.

## Decision

Approve a versioned, per-ID administrator remediation manifest as a typed historical governance fact.

- Every target and Namespace is explicitly enumerated.
- Wildcards, default Namespace and selector-based bulk assignment are forbidden.
- An active system administrator and change ticket are required.
- The platform validates all final typed relationships before writing.
- Apply is atomic, conditional, replay-safe and writes audit receipts in the same transaction.
- A direct assignment can establish historical ownership even when no trustworthy WorkTrace link exists; it does not
  create or imply a telemetry relationship.

## Consequences

Large legacy databases require an explicit mapping exercise. This is intentionally slower than defaulting all rows,
but preserves tenant isolation and makes the contract decision reviewable. Incorrect or incomplete manifests fail
before any write.

## Verification

- Manifest JSON Schema/Golden tests.
- Service tests for permissions, cross-tenant rejection, dry-run, atomicity and replay.
- MySQL 8.x transaction tests.
- Contract preflight must be zero before non-null DDL.
