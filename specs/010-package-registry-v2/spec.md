# Specification: Agent Package Registry v2

**Status**: complete — see [2026-08-04 evidence](evidence/package-registry-v2-20260804.md)
**Epic**: E04 / S7 / M4a
**Created**: 2026-08-04

## 1. Outcome

DuckDock must own a provider-neutral, Namespace-scoped registry for immutable,
deployable Agent packages. A registered PackageVersion is the exact subject
that E05 Release Control will bind to a Deployment revision and evaluation
evidence. Existing Skill Registry records remain compatible and may be
referenced as package components, but they are not themselves Agent packages.

E04 is complete only when all six slices below have implementation, migration,
API, test, and real-development evidence.

## 2. Required slices

| Slice | Required outcome |
|---|---|
| PKG-01 | Namespace-scoped `AgentPackage` identity and immutable semantic `AgentPackageVersion`; duplicate names/versions and cross-tenant Agent references fail closed. |
| PKG-02 | Strict Agent Package v2 manifest validation, deterministic canonical JSON/SHA-256, exact implicit component nodes, explicit dependency edges, missing-node/self-edge/duplicate-edge/cycle rejection, and deterministic graph digest. |
| PKG-03 | CycloneDX JSON or SPDX JSON SBOM verification; canonical document digest must match the manifest and every manifest artifact SHA-256 must occur in the SBOM. SBOM bytes live in MinIO; MySQL stores only bounded metadata and the content-addressed object key. |
| PKG-04 | Namespace-admin Ed25519 trust keys with canonical public-key fingerprint and explicit revocation. Version registration verifies a detached signature over the exact canonical manifest bytes using an active trusted key. Revocation blocks future versions without rewriting historical verification evidence. |
| PKG-05 | Immutable source repository/revision/builder/build timestamps and provenance digest, content-free Audit/Outbox events, exact PackageVersion lookup/verification, and an optional nullable PackageVersion pin on legacy-compatible `AgentDeployment`; cross-tenant or unverified pins are rejected. |
| PKG-06 | Package Registry UI for identities, trust keys, signed-version registration, version/graph/SBOM/provenance inspection, plus full backend/frontend, real MySQL/MinIO, HTTP, and browser validation. |

## 3. Contract and canonicalization

- `schema_version` is exactly `2.0`; unknown fields are rejected.
- The G0 contract remains backward compatible. `component_graph` and `sbom`
  are additive contract fields, but the E04 registration profile requires both.
- `manifest.package_id` must equal the selected DuckDock Package public ID.
- `manifest.namespace_id` and `manifest.agent.asset_id` must equal the immutable
  Namespace and Agent refs frozen when the Package identity was created.
- Package versions use semantic version strings and are unique per Package.
- Canonical JSON is UTF-8, key-sorted, compact JSON with Unicode preserved.
  The detached Ed25519 signature covers these exact bytes.
- Each component ref is `type:asset_id:version_id`. Graph endpoints must resolve
  to these implicit nodes. Every directed relation is acyclic.
- Manifest, graph, provenance, SBOM document, public key, and signature each
  have independent SHA-256 digests where applicable.

## 4. SBOM boundary

- Accepted profiles are CycloneDX JSON and SPDX JSON only.
- Maximum canonical SBOM size is 2 MiB; maximum component/package count is
  2,048.
- CycloneDX SHA-256 hashes are read from `components[].hashes[]`; SPDX hashes
  are read from `packages[].checksums[]`.
- Every manifest artifact digest must be present. Extra transitive packages are
  allowed and are reflected in the component count.
- API, Audit, and Outbox never embed the SBOM document. Download uses a bounded,
  expiring MinIO URL after Namespace authorization.

## 5. Signature and trust boundary

- Only Ed25519 public keys in SubjectPublicKeyInfo PEM are accepted.
- Private keys never enter DuckDock.
- Key registration and revocation require Namespace admin; package and version
  creation require Namespace write access.
- A key must be active at registration time. Invalid base64, wrong signature
  length, bad signature, unknown key, revoked key, or cross-tenant key is a
  hard rejection with no PackageVersion, SBOM row, Audit, or Outbox commit.
- Historical records preserve the key fingerprint and verified-at timestamp.
  A later revocation is visible but does not falsify the historical fact that
  the signature was valid at registration time.

## 6. Immutability, idempotency, and compatibility

- PackageVersion, component, edge, SBOM metadata, signature evidence, and
  provenance are append-only. The API has no update/delete operation.
- A Namespace-scoped idempotency key replays only when manifest digest, signing
  key, signature digest, and SBOM digest are identical; otherwise it conflicts.
- Existing Skill, SkillVersion, public distribution, Deployment, and Release
  evaluation APIs remain operational.
- `AgentDeployment.package_version_id` is nullable for historical rows. When a
  new deployment supplies a PackageVersion pin it must resolve to the same
  Namespace and an immutable verified version. E05 candidates must require the
  pin; E04 does not yet promote or deploy anything.

## 7. Security and privacy

- Manifest annotations remain allowlisted and bounded. Prompts, messages,
  completions, tool arguments/results, credentials, private keys, and raw
  runtime content are forbidden.
- Artifact URIs are metadata references, not fetched during registration.
- Outbox payloads contain only public IDs, status, bounded counts, timestamps,
  and digests. Audit details never contain PEM, signature value, full manifest,
  or SBOM content.
- Tenant authorization is derived from the authenticated DuckDock user and
  database relationships, never from manifest identifiers.

## 8. Required verification

1. Unit/service/API tests cover canonicalization, graph cycles, cross-tenant
   refs, SBOM completeness, Ed25519 positive/negative/revoked lanes,
   idempotency, immutability, download authorization, and safe events.
2. Migration tests cover fresh and populated MySQL, constraints/indexes, the
   nullable legacy Deployment pin, and guarded downgrade.
3. Real development validation creates an Ed25519 key outside DuckDock,
   registers a package, stores a matching real SBOM in MinIO, registers and
   re-verifies a signed version, pins it to a Deployment, and verifies UI and
   Outbox/Audit privacy.
4. Full backend/frontend gates and `alembic check` pass without raising the
   repository mypy baseline.

## 9. Explicit non-goals

- E04 does not create Environment, ReleaseCandidate, Promotion, Canary,
  DeploymentReceipt, or Rollback records; those are E05.
- E04 does not sign on behalf of CI and never stores a private key.
- E04 does not replace Skill Registry or distribute raw package artifacts.
- E04 does not auto-activate a Deployment or write to Langfuse/Hermes.
