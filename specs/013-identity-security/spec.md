# DuckDock 2.0 Identity / Security

**Status**: Completed
**Epic**: E07
**Gate**: G5 Identity seam / G6 Security lane
**Date**: 2026-08-04

## 1. Goal

Provide a vendor-neutral identity lifecycle around the existing OIDC/LDAP JIT
mapping. A directory can provision or disable a linked person without gaining a
DuckDock user session; a disable event must atomically revoke human-bound
workload credentials, remove live access and create evidence-driven handover
cases. Reporter service/device identities and package signing-key rotation stay
independent from the IdP implementation and from Langfuse.

## 2. Security boundaries

- SCIM uses a dedicated, one-time-returned credential bound to exactly one SSO
  provider. It cannot call normal DuckDock APIs.
- DuckDock stores only token hashes and Ed25519 public keys. Private signing
  material never crosses the API boundary.
- Directory events are idempotent and metadata-only. Raw SCIM payloads, names,
  email addresses, tokens and comments are excluded from Audit and Outbox.
- A disabled user immediately fails normal JWT authentication because
  `users.is_active=false`; namespace memberships and scoped role bindings are
  removed in the same transaction.
- Existing signed packages remain verifiable after trust-key rotation because
  the old public-key record and package fingerprint are retained.

## 3. Acceptance slices

| ID | Acceptance |
|---|---|
| IAM-01 | Dedicated provider-bound SCIM credentials support issue/list/revoke, expiry and one-time secret return. |
| IAM-02 | SCIM User PATCH accepts deterministic `active` lifecycle changes with required external event id, payload digest and idempotent replay/conflict semantics. |
| IAM-03 | Disable locks the linked identity, disables the DuckDock user and offboards the handover profile in one transaction. |
| IAM-04 | Disable revokes all active user-bound Reporter/device/service credentials and legacy Runtime tokens, and removes Namespace/Role access. |
| IAM-05 | Disable creates or reuses one `employee_offboarding` HandoverCase per affected Namespace with the configured active receiver/fallback owner and exact Runtime scope. |
| IAM-06 | ReporterCredential is exposed as a typed DEVICE or SERVICE workload identity with opaque public id, least-privilege scopes, rotation chain and revocation. |
| IAM-07 | PackageSigningKey supports atomic external-public-key rotation: register successor, revoke predecessor, preserve lineage and emit content-safe events. |
| IAM-08 | Directory, workload-identity and key-rotation security actions write Audit plus transactional content-safe Outbox events. |
| IAM-09 | Real or staging-equivalent directory disable proves active credential rejection and automatic Handover 2.0 trigger against local MySQL/HTTP/browser. |

## 4. SCIM compatibility lane

The first supported SCIM surface is `PATCH /api/v2/scim/v2/Users/{subject}`
with the standard `Operations` form for `active`. `X-DuckDock-Event-Id` is
required so upstream retry behavior is safe. Discovery documents and broader
SCIM attribute/group synchronization are compatibility extensions; they cannot
weaken the lifecycle transaction or reintroduce raw directory content into
evidence events.

## 5. Handover seam

An offboarding case is generated only after affected Namespace and Runtime
scope are frozen. If no active receiver/fallback is configured, the lifecycle
event still disables access and revokes credentials, but records the missing
assignment and creates a draft case with the Namespace owner when that owner is
an active non-subject user. Readiness continues to fail closed until an active
receiver is present. This closes HO-08 without making Handover depend on a
specific IdP.

## 6. Migration / rollback

Revision `0061` adds directory credential/event evidence, identity-link
lifecycle state, workload identity metadata and signing-key lineage. Once a
directory lifecycle event or key rotation exists, downgrade is refused because
removing the evidence would invalidate a security/offboarding audit trail.

IAM-01 through IAM-09 are complete. Reproducible evidence is recorded in [identity-security-e07-20260804.md](evidence/identity-security-e07-20260804.md).
