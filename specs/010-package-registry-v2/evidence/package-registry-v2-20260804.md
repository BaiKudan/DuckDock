# E04 Agent Package Registry v2 Evidence — 2026-08-04

## Outcome

E04 is complete. DuckDock now owns a provider-neutral, Namespace-scoped and
append-only Agent Package Registry whose exact PackageVersion is suitable as
the release subject for E05:

1. an `AgentPackage` binds one existing Agent asset to an immutable Namespace
   identity;
2. a strict v2 manifest freezes exact component versions, a validated acyclic
   component graph, telemetry requirements, evaluation policy and build
   provenance;
3. a Namespace-admin trust store accepts Ed25519 public keys only, while the
   detached signature covers the canonical UTF-8 manifest bytes;
4. a CycloneDX/SPDX SBOM must match its canonical digest and cover every
   manifest artifact SHA-256 before its bytes are written to MinIO;
5. legacy Deployments remain readable while a new Deployment may explicitly
   pin one same-Namespace VERIFIED PackageVersion;
6. `/packages` exposes Package identities, public-key lifecycle, signed version
   registration, DAG/SBOM/provenance inspection, evidence re-verification and
   authorized SBOM download.

No private key enters DuckDock. E04 does not create a ReleaseCandidate, promote
an Environment, or invoke Hermes/Langfuse; those control operations remain E05.

## Implementation boundary

Revision `20260804_0057` creates:

- `agent_packages`
- `package_signing_keys`
- `agent_package_versions`
- `package_components`
- `package_dependencies`
- `package_sboms`
- nullable `agent_deployments.package_version_id`

The v2 API adds Agent Package, PackageVersion, signing-key, canonicalization,
full verification and SBOM-download surfaces. PackageVersion registration
emits content-free Audit and transactional Outbox evidence. The existing Skill
Registry is unchanged and remains available as a component source rather than
being treated as an Agent Package.

## Automated evidence

- Backend full suite: `980 passed, 19 skipped`.
- Focused Package/Deployment/contract/migration suite: `33 passed, 1 skipped`.
- Frontend full suite: `50 passed`; the Package Registry test renders immutable
  component/SBOM evidence and drives full evidence re-verification.
- Frontend lint: `0 errors`, `9` pre-existing warnings; production build passed.
- Targeted Ruff and Python compile passed.
- Repository-wide mypy remained exactly at the frozen `82`-error ceiling; the
  baseline was not relaxed.
- Langfuse v4 compatibility gate remained green at `60 passed`, confirming that
  the Package Registry is decoupled from the replaceable telemetry/evaluation
  provider.
- Development MySQL upgraded from `20260804_0056` to `20260804_0057 (head)`;
  `alembic check` reported no model/schema drift.

## Real HTTP + MySQL + MinIO + Deployment validation

The rebuilt local stack generated an Ed25519 private key only inside the
validation process, registered the public key over HTTP, canonicalized and
signed a two-component Hermes manifest, and registered the matching CycloneDX
document through the public v2 API.

Retained manual-verification fixture:

- Namespace: `hermes-macmini-live` (`6`)
- Package: `pkg_05f44cc044fd4526b7bcc87ff4285353`
- Package name: `hermes-e04-ready-20260804020013`
- PackageVersion: `pkgv_35c6af5165f54f69aad2c6d8acb59175` / `1.0.0`
- Manifest digest:
  `728e4e582b4a4cfa8752793c46b22962c3ec796e7f679f074cef75ba98c25e98`
- Graph digest:
  `016ab7ddec2ae52bfa6d1b1b1278bb7b54bfcc9e2ecece3bef6572e9d7fead0c`
- Signing key: `pkey_5596a3c7bc024c009387130a7bbae5b5`
- Public-key fingerprint:
  `701cfe48f92fa1500ec9de3eada327826985753721a7f3dd300b91f7dd956231`
- SBOM: `psbom_071f5534be524b03bd2ddd029eaa9568`
- SBOM digest:
  `f1aa67f3420121e73686a7bf55d7d9938a5dd54d34ac209cfc09efb3a7e8f019`
- Deployment: `dep_e9e60bbea96c499999874579c2862461`
- Deployment result: `ACTIVE`, exact PackageVersion pin matches the ID above

The real graph contains two nodes (`runtime_bundle`, `config`) and one
`depends_on` edge. The real SBOM contains two components. API registration and
an immediate identical retry both returned HTTP `201` and the same
PackageVersion ID. Full re-verification returned `verified=true` for manifest,
signature, graph, provenance, SBOM digest and component coverage.

The authorized download endpoint produced a bounded MinIO URL using the current
Mac endpoint `192.168.0.170:9000`. Fetching that URL returned HTTP `200`, media
type `application/vnd.cyclonedx+json`, `513` bytes, and the exact registry
SHA-256 above.

## Idempotency race found and closed

The first real immediate replay exposed a MySQL `REPEATABLE READ` visibility
race: FastAPI can send the first response immediately before its yield-
dependency commit finishes, allowing a retry transaction to miss the first row
and then lose the unique-key race. The old error path also deleted the shared
content-addressed SBOM key.

The service now rolls back the losing snapshot, re-reads the unique winner and
returns it only when Package, semantic version, manifest, key, signature and
SBOM evidence all match. It never deletes a content-addressed SBOM after a
database race, and exact replay rewrites the signed SBOM bytes so a missing
object is safely repaired. A focused regression verifies this repair path. The
second real run proved the immediate `201/201` replay and retained a valid
MinIO object.

The disposable first Package/key/version, its two component rows, dependency,
SBOM row, three Audit rows, three published Outbox rows and object key were then
deleted under the owner's explicit development-test-data cleanup authorization.
All exact target counts were re-read as zero; the operation is not recoverable.

## Audit, Outbox and privacy evidence

The retained Package/key/version produced three transactional domain events,
all observed as `PUBLISHED`. Independent inspection found zero occurrences of:

- public-key PEM or the detached signature value;
- full manifest JSON or full SBOM JSON;
- prompts, messages, model input/output, tool arguments/results or private-key
  markers.

Audit contains only bounded IDs, digests, fingerprints and counts. Outbox
contains only the same low-sensitivity release evidence. MySQL stores SBOM
metadata and the internal object key, not the SBOM document.

## Browser and local deployment evidence

A real browser logged into the rebuilt application, opened `Agent Packages`,
loaded Namespace `hermes-macmini-live`, and rendered the retained Package,
ACTIVE public key, VERIFIED v1.0.0, two component nodes, dependency edge,
CycloneDX metadata, provenance and Ed25519 signature digest. Clicking
`复验全部证据` rendered:

```text
signature=true · sbom digest=true · component coverage=true · key=ACTIVE
```

The temporary browser administrator and its Namespace membership were logged
out and physically deleted after validation. The retained Package and ACTIVE
Deployment remain available for the owner to inspect. The development stack is
left running at frontend `http://127.0.0.1:5174`, backend
`http://127.0.0.1:8801`, Langfuse `http://127.0.0.1:3200` and MinIO console
`http://127.0.0.1:9001`.
